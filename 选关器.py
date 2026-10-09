"""Rats! 关卡选择器 —— 自由选关 1~40 关。

原理
----
游戏主菜单的按钮命中测试有缺陷（i 初始化为 0 后从不递增，整段无循环），
所以后三章的按钮点不动。但章节切换的实际执行代码是完整的：

    0x409833 / 0x40985F / 0x40988B / 0x4098B7
        cmp [0x4199C4], 1        ; 门控（启动时已被 0x407260 置 1）
        jne <跳过>
        mov [0x4199A8], <关卡索引>   ; Ch1=0  Ch2=10  Ch3=20  Ch4=30
        mov [0x4199A4], <章节号>     ; 1 / 2 / 4 / 8
        call 0x40A7C3                ; 加载关卡
    ...
    0x40976D  WM_COMMAND 分发：wParam 低字节 - 0xD2 查跳转表 0x40A777

本启动器跳过长键命中测试，直接发 `WM_COMMAND(0xD2 + i)`，
效果等同于命中测试通过后游戏自己发出的那条消息。

试玩限制说明
------------
游戏是 30 关免费版（`DLG_DEMO`）。原版会在“通关计数器 [0x419598] > 7
且 当前关卡索引 [0x4199A8] >= 9”时弹试玩结束框，导致后三章过关后
进不了下一关。此问题已通过修改 Rats.exe（`rats_demo_patch.py` 把判断
阈值 9 改成 99）根治，本启动器无需再做内存处理。

自由选关
--------
章节切换的四个分支把关卡索引**写死**成每章第一关：

    0x40983C  mov [0x4199A8], 0     ; 第1章 -> 第1关
    0x409868  mov [0x4199A8], 0xA   ; 第2章 -> 第11关
    0x409894  mov [0x4199A8], 0x14  ; 第3章 -> 第21关
    0x4098C0  mov [0x4199A8], 0x1E  ; 第4章 -> 第31关
    ... 然后 push <章节序号>; call 0x40A7C3（关卡加载，读 [0x4199A8]）

所以要跳到任意一关，只需**运行时改写对应分支 imm32 立即数**，再发
WM_COMMAND 让游戏主线程自己加载即可——无需注入、无线程安全问题，
跳完还原原值以免影响游戏内菜单。

既然能任意跳关，GUI 里就不再保留四个章节快捷按钮了。

注意：关卡加载函数 0x40A884 会把 [0x4199C4] 清零，而章节切换门控
要求它 = 1，故每次跳关前都要补写 C4=1。

路径一律用相对路径 `./`：PyInstaller 单文件模式会把脚本解包到
`%TEMP%` 下的 `_MEIxxxxx`，`__file__` 指向那里而非 exe 所在目录。
以 `./` 开头 + `cwd=GAME_DIR` 才能保证找到同目录的 Rats.exe。
GAME_DIR 则用 `sys.executable`（打包后）或 `__file__`（源码）定位。
"""
import ctypes, ctypes.wintypes as wt
import subprocess, time, struct, threading, os, sys

# 相对路径 —— 配合 cwd=GAME_DIR 使用
EXE = os.path.join('.', 'Rats.exe')

# 监控地址
ADDR_LOADED = 0x415570    # 真正加载的关卡（-1 = 未加载）
ADDR_IDX    = 0x4199A8    # 章节索引
ADDR_CHAP   = 0x4199A4    # 章节号
ADDR_C4     = 0x4199C4    # 已购解锁标志

# 自由选关：章节切换四个分支里 `mov [0x4199A8], <imm32>` 的立即数地址。
# 游戏原本写死成每章第一关（0/10/20/30），运行时改写即可跳到该章任意一关。
#   第1章 0x40983C 指令 -> imm32 @ 0x409842（原值 0）
#   第2章 0x409868 指令 -> imm32 @ 0x40986E（原值 10）
#   第3章 0x409894 指令 -> imm32 @ 0x40989A（原值 20）
#   第4章 0x4098C0 指令 -> imm32 @ 0x4098C6（原值 30）
CHAP_IMM32 = {0: 0x409842, 1: 0x40986E, 2: 0x40989A, 3: 0x4098C6}
CHAP_DEFAULT = {0: 0, 1: 10, 2: 20, 3: 30}   # 原值，用于跳完还原
CHAP_BIT     = {0: 1, 1: 2, 2: 4, 3: 8}      # [0x4199A4] 章节号位掩码
MAX_LEVEL    = 40                             # 关卡总数（免费10 + 注册30）

u32 = ctypes.WinDLL('user32', use_last_error=True)
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
WP = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

# ---- 进程访问权限 ----
# 跳关要改“代码段”的立即数，必须先 VirtualProtectEx 放开页保护，
# 而 VirtualProtectEx 强制要求句柄带 PROCESS_VM_OPERATION(0x0008)；
# 只用 READ|WRITE|QUERY 会直接 ERROR_ACCESS_DENIED —— 这是最初跳关失败的原因。
PROC_ACCESS = 0x0008 | 0x0010 | 0x0020 | 0x0400   # OPERATION|READ|WRITE|QUERY

# ---- 显式声明 argtypes/restype ----
# 64 位 Python 下若不声明，ctypes 默认按 C int 传参，句柄有被截断的风险。
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wt.BOOL
k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wt.BOOL
k32.VirtualProtectEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                                 wt.DWORD, ctypes.POINTER(wt.DWORD)]
k32.VirtualProtectEx.restype = wt.BOOL
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.WaitForSingleObject.restype = wt.DWORD

# 启动时所在目录（exe 自身所在目录），供 cwd 使用
if getattr(sys, 'frozen', False):
    GAME_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    GAME_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 底层工具

def find_window(pid, title):
    res = []
    def cb(h, lp):
        pr = wt.DWORD(0)
        u32.GetWindowThreadProcessId(h, ctypes.byref(pr))
        if pr.value == pid:
            t = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(h, t, 256)
            if t.value == title:
                res.append(h)
        return True
    u32.EnumWindows(WP(cb), 0)
    return res[0] if res else None


def find_child_text(parent, text):
    res = []
    def cb(h, lp):
        t = ctypes.create_unicode_buffer(64)
        u32.GetWindowTextW(h, t, 64)
        if t.value == text:
            res.append(h)
        return True
    u32.EnumChildWindows(parent, WP(cb), 0)
    return res


def find_child(parent, cls):
    """按窗口类名找子控件（如 'Button'）。"""
    res = []
    def cb(h, lp):
        c = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, c, 64)
        if c.value == cls:
            res.append(h)
        return True
    u32.EnumChildWindows(parent, WP(cb), 0)
    return res


def press_button(btn_hwnd):
    """用 BM_CLICK 消息按按钮，不移动鼠标、不依赖焦点。"""
    u32.SendMessageW(btn_hwnd, 0x00F5, 0, 0)   # BM_CLICK


def kill_existing():
    """杀掉已在运行的 Rats.exe，避免多实例互相干扰。"""
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p

    class PE(ctypes.Structure):
        _fields_ = [('dwSize', wt.DWORD), ('cntUsage', wt.DWORD),
                    ('th32ProcessID', wt.DWORD), ('th32DefaultHeapID', ctypes.c_void_p),
                    ('th32ModuleID', wt.DWORD), ('cntThreads', wt.DWORD),
                    ('th32ParentProcessID', wt.DWORD), ('pcPriClassBase', ctypes.c_long),
                    ('dwFlags', wt.DWORD), ('szExeFile', ctypes.c_char * 260)]

    killed = 0
    for _ in range(4):
        s = k32.CreateToolhelp32Snapshot(0x00000002, 0)
        pe = PE(); pe.dwSize = ctypes.sizeof(PE)
        pids = []
        if k32.Process32First(s, ctypes.byref(pe)):
            while True:
                if pe.szExeFile.decode('latin1', 'ignore').lower() == 'rats.exe':
                    pids.append(pe.th32ProcessID)
                if not k32.Process32Next(s, ctypes.byref(pe)):
                    break
        k32.CloseHandle(s)
        if not pids:
            break
        for pid in pids:
            h = k32.OpenProcess(0x0001 | 0x00100000, False, pid)  # TERMINATE|SYNCHRONIZE
            if h:
                k32.TerminateProcess(h, 0)
                k32.CloseHandle(h)
                killed += 1
        time.sleep(1.0)
    return killed


def read_mem(hproc, addr):
    b = ctypes.create_string_buffer(4)
    c = ctypes.c_size_t(0)
    if k32.ReadProcessMemory(hproc, ctypes.c_void_p(addr), ctypes.byref(b), 4,
                             ctypes.byref(c)) and c.value == 4:
        return struct.unpack('<I', b.raw)[0]
    return None


def write_mem(hproc, addr, val):
    """把 4 字节整数 val 写入进程 addr 地址。返回是否成功。

    注意：lpBuffer 必须指向一个真实缓冲区（byref），
    不能把 val 当指针地址传——否则 WriteProcessMemory 会从无效源读取而失败。
    """
    buf = ctypes.c_int32(val)
    n = ctypes.c_size_t(0)
    ok = k32.WriteProcessMemory(hproc, ctypes.c_void_p(addr),
                                ctypes.byref(buf), 4, ctypes.byref(n))
    return bool(ok) and n.value == 4


def write_code_imm32(hproc, addr, val):
    """改写代码段里的 imm32 立即数（先放开写权限，写完还原）。

    代码段默认是 PAGE_EXECUTE_READ，直接 WriteProcessMemory 会失败，
    所以要先 VirtualProtectEx 临时改成 PAGE_EXECUTE_READWRITE。

    返回 (ok, err_msg)；err_msg 带 GetLastError，便于定位失败原因。
    前提：hproc 必须带 PROCESS_VM_OPERATION，否则 VirtualProtectEx 必失败。
    """
    PAGE_EXECUTE_READWRITE = 0x40
    old = wt.DWORD(0)
    if not k32.VirtualProtectEx(hproc, ctypes.c_void_p(addr), ctypes.c_size_t(4),
                                PAGE_EXECUTE_READWRITE, ctypes.byref(old)):
        return False, 'VirtualProtectEx 失败 (err=%d)' % ctypes.get_last_error()
    try:
        if not write_mem(hproc, addr, val):
            return False, 'WriteProcessMemory 失败 (err=%d)' % ctypes.get_last_error()
    finally:
        back = wt.DWORD(0)
        k32.VirtualProtectEx(hproc, ctypes.c_void_p(addr), ctypes.c_size_t(4),
                             old.value, ctypes.byref(back))
    return True, ''


# ---------------------------------------------------------------- 核心流程

def start_game(log=print, timeout=25):
    """启动游戏 -> 关掉 About -> 等主窗口 -> 打开进程句柄。

    返回 (ok, message, pid, hwnd, hproc)；失败时后三项为 None。
    """
    if not os.path.exists(EXE):          # EXE 是相对路径 './Rats.exe'
        return False, '找不到 %s（工作目录 %s）' % (EXE, os.getcwd()), None, None, None

    killed = kill_existing()
    if killed:
        log('已清理 %d 个旧进程' % killed)

    log('启动游戏...')
    p = subprocess.Popen([EXE], cwd=GAME_DIR)

    # About 对话框先于主窗口出现：先等它、关掉它
    ab = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        ab = find_window(p.pid, 'About')
        if ab:
            break
        time.sleep(0.1)
    if ab:
        oks = find_child_text(ab, 'OK') or find_child(ab, 'Button')
        if oks:
            press_button(oks[0])          # BM_CLICK，不动鼠标
            log('已关闭 About')
            t1 = time.time()
            while time.time() - t1 < 2.0 and find_window(p.pid, 'About'):
                time.sleep(0.1)

    # 再等主窗口
    hwnd = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        hwnd = find_window(p.pid, 'Rats!')
        if hwnd:
            break
        time.sleep(0.2)
    if not hwnd:
        p.kill()
        return False, '等待主窗口超时（%d 秒）' % timeout, p.pid, None, None

    hproc = k32.OpenProcess(PROC_ACCESS, False, p.pid)
    if not hproc:
        return False, 'OpenProcess 失败 (err=%d)' % ctypes.get_last_error(), p.pid, hwnd, None

    u32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    return True, '游戏已启动', p.pid, hwnd, hproc


def launch_into_chapter(cmd, want_level, want_chap, log=print, timeout=25):
    """（兼容旧接口）启动游戏并进入某章第一关。返回 (ok, message, pid)。"""
    ok, msg, pid, hwnd, hproc = start_game(log, timeout)
    if not ok:
        return False, msg, pid
    try:
        if read_mem(hproc, ADDR_C4) != 1:
            write_mem(hproc, ADDR_C4, 1)
            log('已补写解锁标志 C4=1')

        log('发送 WM_COMMAND(0x%02X)...' % cmd)
        u32.SendMessageW(hwnd, 0x0111, cmd, 0)   # WM_COMMAND
        time.sleep(1.8)

        loaded = read_mem(hproc, ADDR_LOADED)
        idx = read_mem(hproc, ADDR_IDX)
        chap = read_mem(hproc, ADDR_CHAP)

        if loaded == want_level and idx == want_level and chap == want_chap:
            u32.SetForegroundWindow(hwnd)
            return True, '已进入：关卡索引=%s 章节号=%s' % (idx, chap), pid
        return False, '未成功：关卡索引=%s 章节号=%s（期望 %s / %s）已加载=%s' % (
            idx, chap, want_level, want_chap, loaded), pid
    finally:
        k32.CloseHandle(hproc)


def jump_to_level(hproc, hwnd, level, log=print, restore=True, wait=1.8):
    """跳到任意关卡（1~40）。返回 (ok, message)。

    原理：把所属章节分支里写死的关卡索引立即数改成目标值，
    再发 WM_COMMAND 让游戏主线程自己加载——无需注入、无线程安全问题。
    """
    idx = level - 1                      # 关卡号 -> 索引（第1关=0）
    if not (0 <= idx < MAX_LEVEL):
        return False, '关卡号超出范围（1~%d）' % MAX_LEVEL

    chap = idx // 10                     # 章节序号 0~3
    imm_addr = CHAP_IMM32[chap]
    cmd = 0xD2 + chap

    # 门控：关卡加载函数 0x40A884 会把 [0x4199C4] 清零，
    # 而章节切换要求它 = 1，所以每次跳关前必须补写。
    if read_mem(hproc, ADDR_C4) != 1:
        write_mem(hproc, ADDR_C4, 1)
        log('已补写解锁标志 C4=1')

    log('改写第%d章分支立即数 -> %d' % (chap + 1, idx))
    okc, err = write_code_imm32(hproc, imm_addr, idx)
    if not okc:
        return False, '改写代码立即数失败：%s' % err

    try:
        log('发送 WM_COMMAND(0x%02X)...' % cmd)
        u32.SendMessageW(hwnd, 0x0111, cmd, 0)   # WM_COMMAND
        time.sleep(wait)

        loaded = read_mem(hproc, ADDR_LOADED)
        cur = read_mem(hproc, ADDR_IDX)
        chapv = read_mem(hproc, ADDR_CHAP)

        if cur == idx and loaded == idx and chapv == CHAP_BIT[chap]:
            u32.SetForegroundWindow(hwnd)
            return True, '已进入第 %d 关（索引=%d 章节号=%d）' % (level, cur, chapv)
        return False, '未成功：索引=%s 已加载=%s 章节号=%s（期望 %d / %d / %d）' % (
            cur, loaded, chapv, idx, idx, CHAP_BIT[chap])
    finally:
        if restore:
            # 还原成章节第一关，避免影响游戏内菜单的章节按钮
            write_code_imm32(hproc, imm_addr, CHAP_DEFAULT[chap])


# ---------------------------------------------------------------- GUI

def run_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title('Rats! 关卡选择器')
    root.resizable(False, False)
    root.configure(bg='#1a1a2e')

    tk.Label(root, text='Rats!  1996', bg='#1a1a2e', fg='#e8e8e8',
             font=('Consolas', 11)).pack(pady=(16, 2))
    tk.Label(root, text='关卡选择器 —— 自由选关 1~40 关',
             bg='#1a1a2e', fg='#8888aa', font=('Consolas', 9)).pack(pady=(0, 12))

    status = tk.Label(root, text='就绪', bg='#1a1a2e', fg='#88ff88',
                      font=('Consolas', 10), wraplength=360, justify='center')
    status.pack(pady=(0, 12))

    # 当前游戏会话（启动一次后可反复跳关，不必每次重启）
    sess = {'pid': None, 'hwnd': None, 'hproc': None}
    busy = {'flag': False}

    def say(msg, ok=None):
        status.config(text=('OK  ' + msg) if ok else (('失败  ' + msg) if ok is False else msg),
                      fg=('#88ff88' if ok else ('#ff6666' if ok is False else '#ffff88')))
        root.update()

    def ensure_started():
        """确保游戏已启动；已有会话则复用。返回是否可用。"""
        if sess['hproc']:
            # 校验进程还活着
            h = k32.OpenProcess(0x00100000, False, sess['pid'])
            if h:
                rc = k32.WaitForSingleObject(h, 0)
                k32.CloseHandle(h)
                if rc != 0:        # 仍在运行
                    return True
            # 进程已退出，清理旧句柄
            try:
                k32.CloseHandle(sess['hproc'])
            except Exception:
                pass
            sess.update(pid=None, hwnd=None, hproc=None)

        ok, msg, pid, hwnd, hproc = start_game(log=say)
        if not ok:
            say(msg, False)
            return False
        sess.update(pid=pid, hwnd=hwnd, hproc=hproc)
        return True

    def do_jump(level):
        if busy['flag']:
            return
        busy['flag'] = True
        say('正在进入第 %d 关 ...' % level)

        def work():
            try:
                if not ensure_started():
                    return
                ok, msg = jump_to_level(sess['hproc'], sess['hwnd'], level, log=say)
                say(msg, ok)
            finally:
                busy['flag'] = False

        threading.Thread(target=work, daemon=True).start()

    # ---- 自由选关区 ----
    frm = tk.Frame(root, bg='#1a1a2e')
    frm.pack(pady=(0, 12))

    tk.Label(frm, text='关卡', bg='#1a1a2e', fg='#e8e8e8',
             font=('Consolas', 10)).pack(side='left', padx=(0, 6))
    level_var = tk.StringVar(value='12')
    sp = tk.Spinbox(frm, from_=1, to=MAX_LEVEL, width=5, textvariable=level_var,
                    font=('Consolas', 11, 'bold'), justify='center')
    sp.pack(side='left', padx=(0, 8))
    tk.Button(frm, text='跳到该关', width=12, bg='#3d5a80', fg='#e8e8e8',
              activebackground='#4d7aa0', activeforeground='#ffffff',
              relief='raised', bd=2, font=('Consolas', 10, 'bold'), cursor='hand2',
              command=lambda: do_jump(int(level_var.get()))
              ).pack(side='left')

    root.mainloop()


if __name__ == '__main__':
    if '--diag' in sys.argv:
        print('=== Rats! 启动器诊断（只读 + 写回原值测试）===')
        print('GAME_DIR =', GAME_DIR)
        print('PROC_ACCESS = 0x%X (需含 PROCESS_VM_OPERATION=0x8)' % PROC_ACCESS)
        ok, msg, pid, hwnd, hproc = start_game(log=print)
        print('start_game: %s (%s) pid=%s hwnd=%s hproc=%s' % (ok, msg, pid, hwnd, hproc))
        if ok:
            print('ADDR_C4   =', read_mem(hproc, ADDR_C4))
            print('ADDR_IDX  =', read_mem(hproc, ADDR_IDX))
            print('ADDR_CHAP =', read_mem(hproc, ADDR_CHAP))
            print('ADDR_LOADED =', read_mem(hproc, ADDR_LOADED))
            for chap in range(4):
                a = CHAP_IMM32[chap]
                raw = ctypes.create_string_buffer(4)
                n = ctypes.c_size_t(0)
                rok = k32.ReadProcessMemory(hproc, ctypes.c_void_p(a),
                                            ctypes.byref(raw), 4, ctypes.byref(n))
                val = struct.unpack('<i', raw.raw)[0] if rok else None
                wok, werr = write_code_imm32(hproc, a, CHAP_DEFAULT[chap])
                print('  第%d章 imm32@%08X 读=%s(期望%d) 写回原值=%s %s'
                      % (chap + 1, a, val, CHAP_DEFAULT[chap], wok, werr))
            k32.CloseHandle(hproc)
        print('=== 诊断结束 ===')
    elif '--test' in sys.argv:
        print('=== Rats! 启动器自检 ===')
        print('GAME_DIR =', GAME_DIR)
        for label, cmd, level, chap in [
            ('第1章', 0xD2, 0, 1), ('第2章', 0xD3, 10, 2),
            ('第3章', 0xD4, 20, 4), ('第4章', 0xD5, 30, 8)]:
            print('--- %s ---' % label)
            ok, msg, pid = launch_into_chapter(cmd, level, chap)
            print('  => %s (%s)' % ('OK' if ok else 'FAIL', msg))
            if pid:
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
            time.sleep(1.0)
            kill_existing()
    else:
        run_gui()
