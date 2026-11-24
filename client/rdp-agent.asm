; ==============================================================================
; RdpNet Agent: 100% Standalone Native Windows PE Executable
; Language: Flat Assembler (FASM) x86
; Features:
;   - Zero Python / Zero external dependencies / Zero drivers / Zero admin
;   - Host Mode (with Windows Registry auto-detection of custom RDP port)
;   - Viewer Mode (local listener for mstsc.exe + optional -mstsc auto-launch)
;   - Full RdpNet binary protocol ('RN') + dedicated wire-speed socket piping
;   - INI configuration (auto-loaded next to EXE or CWD) + CLI override
;   - Silent background mode (-silent) + automatic reconnect on drop
; ==============================================================================

format PE console
entry start

include 'win32a.inc'

; Constants
MSG_HELLO          = 01h
MSG_WELCOME        = 02h
MSG_HEARTBEAT      = 03h
MSG_TUNNEL_REQUEST = 10h
MSG_TUNNEL_ASSIGN  = 11h
MSG_TUNNEL_START   = 12h
MSG_TUNNEL_ERROR   = 13h

SD_BOTH            = 2
SOL_SOCKET         = 0FFFFh
SO_REUSEADDR       = 0004h
IPPROTO_TCP        = 6
TCP_NODELAY        = 1
INVALID_SOCK       = -1
KEY_READ_CONST     = 20019h
INFINITE_WAIT      = 0FFFFFFFFh
SW_HIDE_CMD        = 0
SW_SHOW_CMD        = 5

section '.data' data readable writeable
    szBanner        db '==================================================', 13, 10
                    db '     RdpNet Standalone Native Agent v2.0          ', 13, 10
                    db '==================================================', 13, 10, 0
    szUsage         db 'Usage: rdp-agent.exe [options]', 13, 10
                    db '  -server <ip:port>   Relay address (e.g. 1.2.3.4:8443)', 13, 10
                    db '  -net <id>           Network ID', 13, 10
                    db '  -key <secret>       Network pre-shared key', 13, 10
                    db '  -host               Host mode (forwards to local RDP)', 13, 10
                    db '  -viewer             Viewer mode (listens on 127.0.0.1:33890)', 13, 10
                    db '  -name <node>        Node name', 13, 10
                    db '  -target <host>      Target Host name (for viewer mode)', 13, 10
                    db '  -local <port>       Local listening port (default: 33890)', 13, 10
                    db '  -rdp-port <port>    Host RDP port (default: auto-detect)', 13, 10
                    db '  -mstsc              Auto-launch mstsc.exe in viewer mode', 13, 10
                    db '  -silent             Hide console window', 13, 10
                    db '  -config <file>      INI config path (default: rdp-agent.ini)', 13, 10, 0

    ; INI keys
    szIniDefault    db 'rdp-agent.ini', 0
    szSecServer     db 'server', 0
    szKeyAddress    db 'address', 0
    szSecNetwork    db 'network', 0
    szKeyNetId      db 'id', 0
    szKeyNetKey     db 'key', 0
    szSecNode       db 'node', 0
    szKeyRole       db 'role', 0
    szKeyName       db 'name', 0
    szKeyTarget     db 'target', 0
    szKeyLocalPort  db 'local_port', 0
    szKeyRdpPort    db 'rdp_port', 0
    szKeyMstsc      db 'mstsc', 0

    ; Registry path for Windows RDP Port auto-detection
    szRegRdpPath    db 'SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp', 0
    szRegPortVal    db 'PortNumber', 0

    ; Defaults & flags
    szDefServer     db '127.0.0.1:8443', 0
    szDefRole       db 'host', 0
    szLocalhost     db '127.0.0.1', 0
    szEmpty         db 0
    szAuto          db 'auto', 0
    szOne           db '1', 0
    szTrue          db 'true', 0

    szFlagServer    db '-server', 0
    szFlagNet       db '-net', 0
    szFlagKey       db '-key', 0
    szFlagHost      db '-host', 0
    szFlagViewer    db '-viewer', 0
    szFlagName      db '-name', 0
    szFlagTarget    db '-target', 0
    szFlagLocal     db '-local', 0
    szFlagRdpPort   db '-rdp-port', 0
    szFlagConfig    db '-config', 0
    szFlagSilent    db '-silent', 0
    szFlagMstsc     db '-mstsc', 0

    szRoleHost      db 'host', 0
    szRoleViewer    db 'viewer', 0
    szOk            db 'ok', 0
    szKeyStatus     db 'status', 0
    szKeyToken      db 'session_token', 0
    szKeyErrMsg     db 'error_msg', 0
    szKeyMessage    db 'message', 0

    ; Format strings
    szFmtHello      db '{"network_id":"%s","auth_token":"%s","role":"%s","name":"%s"}', 0
    szFmtEmptyJson  db '{}', 0
    szFmtTunnelReq  db '{"target_host":"%s"}', 0
    szFmtSessAuth   db 'SESSION_AUTH %s %s', 10, 0
    szFmtMstscCmd   db 'mstsc.exe /v:127.0.0.1:%d', 0
    szFmtIniAdj     db '%s\%s', 0
    szFmtJsonPat    db '"%s"', 0

    ; Log messages
    szLogInit       db '[*] RdpNet Agent starting: role=%s, net=%s, name=%s, server=%s:%d', 13, 10, 0
    szLogRdpAuto    db '[*] Auto-detected local Windows RDP port: %d', 13, 10, 0
    szLogRdpManual  db '[*] Using local RDP port: %d', 13, 10, 0
    szLogViewerLst  db '[*] Viewer listening on 127.0.0.1:%d (connect via mstsc.exe)', 13, 10, 0
    szLogConnTry    db '[*] Connecting to relay %s:%d...', 13, 10, 0
    szLogConnOk     db '[+] Authenticated with relay! Tunnel ready.', 13, 10, 0
    szLogAuthFail   db '[-] Authentication failed: %s', 13, 10, 0
    szLogReconn     db '[!] Connection lost. Reconnecting in 3s...', 13, 10, 0
    szLogSessHost   db '[+] Bridging incoming RDP session -> 127.0.0.1:%d', 13, 10, 0
    szLogSessView   db '[+] Opening RDP tunnel to target...', 13, 10, 0
    szLogSessErr    db '[-] Tunnel error: %s', 13, 10, 0
    szLogErrArgs    db '[-] Error: -net and -key must be specified (via CLI or rdp-agent.ini).', 13, 10, 0
    szLogBindFail   db '[-] Error: Failed to bind local viewer port 127.0.0.1:%d', 13, 10, 0

    ; Configuration state
    server_full     db 256 dup (0)
    server_host     db 192 dup (0)
    server_port     dd 8443
    net_buf         db 128 dup (0)
    key_buf         db 128 dup (0)
    role_buf        db 32  dup (0)
    name_buf        db 128 dup (0)
    target_buf      db 128 dup (0)
    config_path     db 512 dup (0)
    exe_dir         db 512 dup (0)
    rdp_port_str    db 32  dup (0)
    mstsc_str       db 16  dup (0)
    local_port      dd 33890
    rdp_port        dd 0
    flag_silent     dd 0
    flag_mstsc      dd 0
    mstsc_launched  dd 0

    ; Escaped JSON buffers
    esc_net         db 256 dup (0)
    esc_key         db 256 dup (0)
    esc_role        db 64  dup (0)
    esc_name        db 256 dup (0)
    esc_target      db 256 dup (0)

    ; Runtime state
    wsaData         WSADATA
    ctrl_sock       dd INVALID_SOCK
    ctrl_alive      dd 0
    app_running     dd 1
    hTunnelEvent    dd 0
    tunnel_ok       dd 0
    tunnel_token    db 128 dup (0)
    tunnel_err_msg  db 256 dup (0)

    cs_ctrl         db 32 dup (0)
    cs_tunnel       db 32 dup (0)

    ; CLI argv storage
    argc            dd 0
    argv            dd 64 dup (0)
    cli_copy        db 2048 dup (0)

section '.code' code readable executable

start:
    invoke  InitializeCriticalSection, cs_ctrl
    invoke  InitializeCriticalSection, cs_tunnel
    invoke  CreateEventA, 0, 1, 0, 0
    mov     [hTunnelEvent], eax

    invoke  WSAStartup, 0202h, wsaData
    test    eax, eax
    jnz     .exit_fail

    ; Parse CLI into argc / argv
    call    parse_cli_args

    ; Determine config path (-config or rdp-agent.ini next to CWD / EXE)
    call    resolve_config_path

    ; Load INI settings
    call    load_ini_config

    ; Apply CLI overrides (overrides INI)
    call    apply_cli_overrides

    ; Hide console if -silent
    cmp     dword [flag_silent], 0
    jz      .show_banner
    invoke  GetConsoleWindow
    test    eax, eax
    jz      .after_banner
    invoke  ShowWindow, eax, SW_HIDE_CMD
    jmp     .after_banner

.show_banner:
    cinvoke printf, szBanner

.after_banner:
    ; Ensure default computer name if name_buf empty
    cmp     byte [name_buf], 0
    jnz     .check_required
    mov     dword [argc], 120
    invoke  GetComputerNameA, name_buf, argc

.check_required:
    cmp     byte [net_buf], 0
    jz      .missing_args
    cmp     byte [key_buf], 0
    jnz     .parse_server_addr

.missing_args:
    cinvoke printf, szUsage
    cinvoke printf, szLogErrArgs
    jmp     .exit_fail

.parse_server_addr:
    ; Split server_full ("ip:port") -> server_host & server_port
    cinvoke strcpy, server_host, server_full
    cinvoke strchr, server_host, ':'
    test    eax, eax
    jz      .default_port
    mov     byte [eax], 0
    inc     eax
    cinvoke atoi, eax
    test    eax, eax
    jle     .default_port
    mov     [server_port], eax
    jmp     .detect_rdp_port

.default_port:
    mov     dword [server_port], 8443

.detect_rdp_port:
    ; If host mode and rdp_port == 0, read from Windows Registry
    cinvoke _stricmp, role_buf, szRoleViewer
    test    eax, eax
    jz      .ready_to_run

    cmp     dword [rdp_port], 0
    jnz     .manual_rdp_port
    call    detect_rdp_port_reg
    mov     [rdp_port], eax
    cinvoke printf, szLogRdpAuto, eax
    jmp     .ready_to_run

.manual_rdp_port:
    cinvoke printf, szLogRdpManual, [rdp_port]

.ready_to_run:
    cinvoke printf, szLogInit, role_buf, net_buf, name_buf, server_host, [server_port]

    ; If viewer mode, spawn local listener thread
    cinvoke _stricmp, role_buf, szRoleViewer
    test    eax, eax
    jnz     .control_loop
    invoke  CreateThread, 0, 0, viewer_listener_thread, 0, 0, 0
    test    eax, eax
    jz      .control_loop
    invoke  CloseHandle, eax

    ; Main persistent control reconnect loop
.control_loop:
    cmp     dword [app_running], 0
    jz      .exit_ok

    call    run_control_session

    cmp     dword [app_running], 0
    jz      .exit_ok

    cinvoke printf, szLogReconn
    invoke  Sleep, 3000
    jmp     .control_loop

.exit_ok:
    invoke  WSACleanup
    invoke  ExitProcess, 0

.exit_fail:
    invoke  WSACleanup
    invoke  ExitProcess, 1

; ==============================================================================
; Control Session
; ==============================================================================
proc run_control_session
    locals
        sock      dd INVALID_SOCK
        hHbThread dd 0
        msg_type  dd 0
        rx_buf    dd 0
        hello_buf dd 0
        val_buf   db 256 dup (0)
    endl

    cinvoke malloc, 65536
    mov     [rx_buf], eax
    cinvoke malloc, 2048
    mov     [hello_buf], eax

    cinvoke printf, szLogConnTry, server_host, [server_port]
    stdcall connect_tcp, server_host, [server_port]
    cmp     eax, INVALID_SOCK
    je      .done
    mov     [sock], eax
    mov     [ctrl_sock], eax

    ; Build escaped JSON MSG_HELLO
    stdcall json_escape, net_buf, esc_net, 250
    stdcall json_escape, key_buf, esc_key, 250
    stdcall json_escape, role_buf, esc_role, 60
    stdcall json_escape, name_buf, esc_name, 250
    cinvoke sprintf, [hello_buf], szFmtHello, esc_net, esc_key, esc_role, esc_name

    stdcall send_frame, [sock], MSG_HELLO, [hello_buf]
    test    eax, eax
    jz      .close_sock

    ; Wait for MSG_WELCOME
    lea     eax, [msg_type]
    stdcall recv_frame, [sock], eax, [rx_buf], 65535
    cmp     eax, 0
    jl      .close_sock
    cmp     dword [msg_type], MSG_WELCOME
    jne     .close_sock

    lea     eax, [val_buf]
    stdcall json_get_str, [rx_buf], szKeyStatus, eax, 255
    test    eax, eax
    jz      .auth_failed
    lea     eax, [val_buf]
    cinvoke _stricmp, eax, szOk
    test    eax, eax
    jnz     .auth_failed

    cinvoke printf, szLogConnOk
    cinvoke fflush, 0
    mov     dword [ctrl_alive], 1

    ; Auto-launch mstsc.exe in viewer mode if requested once
    cinvoke _stricmp, role_buf, szRoleViewer
    test    eax, eax
    jnz     .start_hb
    cmp     dword [flag_mstsc], 0
    jz      .start_hb
    cmp     dword [mstsc_launched], 0
    jnz     .start_hb
    mov     dword [mstsc_launched], 1
    cinvoke sprintf, [hello_buf], szFmtMstscCmd, [local_port]
    invoke  WinExec, [hello_buf], SW_SHOW_CMD

.start_hb:
    invoke  CreateThread, 0, 0, heartbeat_thread, [sock], 0, 0
    mov     [hHbThread], eax

.rx_loop:
    cmp     dword [app_running], 0
    jz      .stop_hb

    lea     eax, [msg_type]
    stdcall recv_frame, [sock], eax, [rx_buf], 65535
    cmp     eax, 0
    jl      .stop_hb

    mov     eax, [msg_type]
    cmp     eax, MSG_HEARTBEAT
    je      .rx_loop

    cmp     eax, MSG_TUNNEL_ASSIGN
    je      .on_tunnel_assign

    cmp     eax, MSG_TUNNEL_START
    je      .on_tunnel_start

    cmp     eax, MSG_TUNNEL_ERROR
    je      .on_tunnel_error

    jmp     .rx_loop

.on_tunnel_assign:
    cinvoke malloc, 128
    test    eax, eax
    jz      .rx_loop
    mov     ebx, eax
    stdcall json_get_str, [rx_buf], szKeyToken, ebx, 127
    test    eax, eax
    jz      .free_tok_fail
    invoke  CreateThread, 0, 0, host_bridge_thread, ebx, 0, 0
    test    eax, eax
    jz      .free_tok_fail
    invoke  CloseHandle, eax
    jmp     .rx_loop

.free_tok_fail:
    cinvoke free, ebx
    jmp     .rx_loop

.on_tunnel_start:
    stdcall json_get_str, [rx_buf], szKeyToken, tunnel_token, 127
    mov     dword [tunnel_ok], 1
    invoke  SetEvent, [hTunnelEvent]
    jmp     .rx_loop

.on_tunnel_error:
    mov     dword [tunnel_ok], 0
    stdcall json_get_str, [rx_buf], szKeyMessage, tunnel_err_msg, 255
    invoke  SetEvent, [hTunnelEvent]
    jmp     .rx_loop

.auth_failed:
    lea     eax, [val_buf]
    stdcall json_get_str, [rx_buf], szKeyErrMsg, eax, 255
    lea     eax, [val_buf]
    cinvoke printf, szLogAuthFail, eax
    jmp     .close_sock

.stop_hb:
    mov     dword [ctrl_alive], 0
    mov     dword [ctrl_sock], INVALID_SOCK
    mov     dword [tunnel_ok], 0
    invoke  SetEvent, [hTunnelEvent]
    cmp     dword [hHbThread], 0
    jz      .close_sock
    invoke  WaitForSingleObject, [hHbThread], 2000
    invoke  CloseHandle, [hHbThread]

.close_sock:
    mov     dword [ctrl_alive], 0
    mov     dword [ctrl_sock], INVALID_SOCK
    invoke  closesocket, [sock]
    cinvoke fflush, 0

.done:
    cinvoke free, [rx_buf]
    cinvoke free, [hello_buf]
    ret
endp

; ==============================================================================
; Heartbeat Thread
; ==============================================================================
proc heartbeat_thread sock
    locals
        ticks dd 0
    endl
.loop:
    mov     dword [ticks], 0
.sleep_step:
    cmp     dword [ctrl_alive], 0
    jz      .exit
    cmp     dword [app_running], 0
    jz      .exit
    invoke  Sleep, 250
    inc     dword [ticks]
    cmp     dword [ticks], 60    ; 60 * 250ms = 15s
    jl      .sleep_step

    cmp     dword [ctrl_alive], 0
    jz      .exit
    stdcall send_frame, [sock], MSG_HEARTBEAT, szFmtEmptyJson
    test    eax, eax
    jnz     .loop

.exit:
    ret
endp

; ==============================================================================
; Host Bridge Thread
; ==============================================================================
proc host_bridge_thread token_ptr
    locals
        vps_sock dd INVALID_SOCK
        rdp_sock dd INVALID_SOCK
        auth_buf db 256 dup (0)
    endl

    cinvoke printf, szLogSessHost, [rdp_port]

    stdcall connect_tcp, server_host, [server_port]
    cmp     eax, INVALID_SOCK
    je      .cleanup
    mov     [vps_sock], eax

    lea     eax, [auth_buf]
    cinvoke sprintf, eax, szFmtSessAuth, [token_ptr], szRoleHost
    lea     eax, [auth_buf]
    cinvoke strlen, eax
    lea     ecx, [auth_buf]
    stdcall send_all, [vps_sock], ecx, eax
    test    eax, eax
    jz      .cleanup

    lea     eax, [auth_buf]
    stdcall recv_line, [vps_sock], eax, 255
    cmp     eax, 2
    jl      .cleanup
    cmp     word [auth_buf], 'OK'
    jne     .cleanup

    stdcall connect_tcp, szLocalhost, [rdp_port]
    cmp     eax, INVALID_SOCK
    je      .cleanup
    mov     [rdp_sock], eax

    stdcall pipe_sockets, [vps_sock], [rdp_sock]
    mov     dword [vps_sock], INVALID_SOCK
    mov     dword [rdp_sock], INVALID_SOCK

.cleanup:
    cmp     dword [vps_sock], INVALID_SOCK
    je      .chk_rdp
    invoke  closesocket, [vps_sock]
.chk_rdp:
    cmp     dword [rdp_sock], INVALID_SOCK
    je      .free_mem
    invoke  closesocket, [rdp_sock]
.free_mem:
    cinvoke free, [token_ptr]
    ret
endp

; ==============================================================================
; Viewer Listener & Client Bridge Threads
; ==============================================================================
proc viewer_listener_thread unused
    locals
        lsock      dd INVALID_SOCK
        csock      dd INVALID_SOCK
        opt_one    dd 1
        addr       sockaddr_in
    endl

    invoke  socket, AF_INET, SOCK_STREAM, IPPROTO_TCP
    cmp     eax, INVALID_SOCK
    je      .bind_err
    mov     [lsock], eax

    lea     eax, [opt_one]
    invoke  setsockopt, [lsock], SOL_SOCKET, SO_REUSEADDR, eax, 4

    lea     edi, [addr]
    xor     eax, eax
    mov     ecx, sizeof.sockaddr_in
    rep stosb

    mov     word [addr.sin_family], AF_INET
    invoke  htons, [local_port]
    mov     word [addr.sin_port], ax
    invoke  inet_addr, szLocalhost
    mov     dword [addr.sin_addr], eax

    lea     eax, [addr]
    invoke  bind, [lsock], eax, sizeof.sockaddr_in
    test    eax, eax
    jnz     .bind_err

    invoke  listen, [lsock], 64
    test    eax, eax
    jnz     .bind_err

    cinvoke printf, szLogViewerLst, [local_port]
    cinvoke fflush, 0

.accept_loop:
    cmp     dword [app_running], 0
    jz      .close_lsock
    invoke  accept, [lsock], 0, 0
    cmp     eax, INVALID_SOCK
    je      .accept_loop
    mov     [csock], eax
    invoke  CreateThread, 0, 0, viewer_client_thread, [csock], 0, 0
    test    eax, eax
    jz      .close_csock
    invoke  CloseHandle, eax
    jmp     .accept_loop

.close_csock:
    invoke  closesocket, [csock]
    jmp     .accept_loop

.bind_err:
    cinvoke printf, szLogBindFail, [local_port]
.close_lsock:
    cmp     dword [lsock], INVALID_SOCK
    je      .exit
    invoke  closesocket, [lsock]
.exit:
    ret
endp

proc viewer_client_thread client_sock
    locals
        vps_sock  dd INVALID_SOCK
        ok_local  dd 0
        tok_local db 128 dup (0)
        req_buf   db 512 dup (0)
        auth_buf  db 256 dup (0)
    endl

    cmp     dword [ctrl_alive], 0
    jz      .fail_close

    cinvoke printf, szLogSessView

    invoke  EnterCriticalSection, cs_tunnel

    cmp     dword [ctrl_alive], 0
    jz      .leave_fail

    invoke  ResetEvent, [hTunnelEvent]
    mov     dword [tunnel_ok], 0
    mov     byte [tunnel_token], 0
    mov     byte [tunnel_err_msg], 0

    cmp     byte [target_buf], 0
    jz      .empty_req
    stdcall json_escape, target_buf, esc_target, 250
    lea     eax, [req_buf]
    cinvoke sprintf, eax, szFmtTunnelReq, esc_target
    jmp     .send_req
.empty_req:
    lea     eax, [req_buf]
    cinvoke strcpy, eax, szFmtEmptyJson

.send_req:
    lea     eax, [req_buf]
    stdcall send_frame, [ctrl_sock], MSG_TUNNEL_REQUEST, eax
    test    eax, eax
    jz      .leave_fail

    invoke  WaitForSingleObject, [hTunnelEvent], 5000
    cmp     eax, 0
    jne     .leave_fail

    mov     eax, [tunnel_ok]
    mov     [ok_local], eax
    lea     eax, [tok_local]
    cinvoke strcpy, eax, tunnel_token
    cmp     dword [ok_local], 0
    jnz     .leave_ok
    cinvoke printf, szLogSessErr, tunnel_err_msg
.leave_ok:
    invoke  LeaveCriticalSection, cs_tunnel

    cmp     dword [ok_local], 0
    jz      .fail_close

    ; Open paired data socket
    stdcall connect_tcp, server_host, [server_port]
    cmp     eax, INVALID_SOCK
    je      .fail_close
    mov     [vps_sock], eax

    lea     eax, [auth_buf]
    lea     ecx, [tok_local]
    cinvoke sprintf, eax, szFmtSessAuth, ecx, szRoleViewer
    lea     eax, [auth_buf]
    cinvoke strlen, eax
    lea     ecx, [auth_buf]
    stdcall send_all, [vps_sock], ecx, eax
    test    eax, eax
    jz      .fail_close

    lea     eax, [auth_buf]
    stdcall recv_line, [vps_sock], eax, 255
    cmp     eax, 2
    jl      .fail_close
    cmp     word [auth_buf], 'OK'
    jne     .fail_close

    stdcall pipe_sockets, [client_sock], [vps_sock]
    ret

.leave_fail:
    invoke  LeaveCriticalSection, cs_tunnel
.fail_close:
    cmp     dword [vps_sock], INVALID_SOCK
    je      .close_cli
    invoke  closesocket, [vps_sock]
.close_cli:
    invoke  closesocket, [client_sock]
    ret
endp

; ==============================================================================
; Bidirectional Wire-Speed Socket Piping
; ==============================================================================
proc pipe_sockets sock_a, sock_b
    locals
        pair_ptr dd 0
        hWorker  dd 0
    endl

    cinvoke malloc, 8
    test    eax, eax
    jz      .close_both
    mov     [pair_ptr], eax
    mov     ecx, [sock_b]
    mov     [eax], ecx
    mov     ecx, [sock_a]
    mov     [eax+4], ecx

    invoke  CreateThread, 0, 0, pipe_worker_thread, [pair_ptr], 0, 0
    mov     [hWorker], eax
    test    eax, eax
    jnz     .run_forward
    cinvoke free, [pair_ptr]
    jmp     .close_both

.run_forward:
    stdcall pipe_one_way, [sock_a], [sock_b]

    ; Shut down both directions so worker unblocks immediately
    invoke  shutdown, [sock_a], SD_BOTH
    invoke  shutdown, [sock_b], SD_BOTH
    invoke  closesocket, [sock_a]
    invoke  closesocket, [sock_b]

    invoke  WaitForSingleObject, [hWorker], INFINITE_WAIT
    invoke  CloseHandle, [hWorker]
    ret

.close_both:
    invoke  closesocket, [sock_a]
    invoke  closesocket, [sock_b]
    ret
endp

proc pipe_worker_thread pair_ptr
    locals
        src dd 0
        dst dd 0
    endl
    mov     eax, [pair_ptr]
    mov     ecx, [eax]      ; src = sock_b
    mov     edx, [eax+4]    ; dst = sock_a
    mov     [src], ecx
    mov     [dst], edx
    cinvoke free, eax
    stdcall pipe_one_way, [src], [dst]
    invoke  shutdown, [src], SD_BOTH
    invoke  shutdown, [dst], SD_BOTH
    ret
endp

proc pipe_one_way src_sock, dst_sock
    locals
        buf dd 0
        n   dd 0
    endl
    cinvoke malloc, 32768
    test    eax, eax
    jz      .exit
    mov     [buf], eax
.loop:
    invoke  recv, [src_sock], [buf], 32768, 0
    cmp     eax, 0
    jle     .done
    mov     [n], eax
    stdcall send_all, [dst_sock], [buf], [n]
    test    eax, eax
    jnz     .loop
.done:
    cinvoke free, [buf]
.exit:
    ret
endp

; ==============================================================================
; TCP & Protocol Framing Helpers
; ==============================================================================
proc connect_tcp host_str, port_num
    locals
        s       dd INVALID_SOCK
        opt_one dd 1
        ip_addr dd 0
        addr    sockaddr_in
    endl

    invoke  inet_addr, [host_str]
    cmp     eax, -1
    jne     .got_ip

    invoke  gethostbyname, [host_str]
    test    eax, eax
    jz      .fail
    mov     eax, [eax+12]       ; h_addr_list
    test    eax, eax
    jz      .fail
    mov     eax, [eax]          ; h_addr_list[0]
    test    eax, eax
    jz      .fail
    mov     eax, [eax]          ; IP dword

.got_ip:
    mov     [ip_addr], eax

    invoke  socket, AF_INET, SOCK_STREAM, IPPROTO_TCP
    cmp     eax, INVALID_SOCK
    je      .fail
    mov     [s], eax

    lea     eax, [opt_one]
    invoke  setsockopt, [s], IPPROTO_TCP, TCP_NODELAY, eax, 4

    lea     edi, [addr]
    xor     eax, eax
    mov     ecx, sizeof.sockaddr_in
    rep stosb

    mov     word [addr.sin_family], AF_INET
    invoke  htons, [port_num]
    mov     word [addr.sin_port], ax
    mov     eax, [ip_addr]
    mov     dword [addr.sin_addr], eax

    lea     eax, [addr]
    invoke  connect, [s], eax, sizeof.sockaddr_in
    test    eax, eax
    jnz     .close_fail

    mov     eax, [s]
    ret

.close_fail:
    invoke  closesocket, [s]
.fail:
    mov     eax, INVALID_SOCK
    ret
endp

proc send_all sock, buf, len
    locals
        sent dd 0
    endl
    mov     dword [sent], 0
.loop:
    mov     eax, [sent]
    cmp     eax, [len]
    jge     .ok
    mov     ecx, [len]
    sub     ecx, eax
    mov     edx, [buf]
    add     edx, eax
    invoke  send, [sock], edx, ecx, 0
    cmp     eax, 0
    jle     .fail
    add     [sent], eax
    jmp     .loop
.ok:
    mov     eax, 1
    ret
.fail:
    xor     eax, eax
    ret
endp

proc recv_exact sock, buf, len
    locals
        got dd 0
    endl
    mov     dword [got], 0
.loop:
    mov     eax, [got]
    cmp     eax, [len]
    jge     .ok
    mov     ecx, [len]
    sub     ecx, eax
    mov     edx, [buf]
    add     edx, eax
    invoke  recv, [sock], edx, ecx, 0
    cmp     eax, 0
    jle     .fail
    add     [got], eax
    jmp     .loop
.ok:
    mov     eax, 1
    ret
.fail:
    xor     eax, eax
    ret
endp

proc recv_line sock, buf, max_len
    locals
        idx dd 0
        ch  db 0
    endl
    mov     dword [idx], 0
.loop:
    mov     eax, [max_len]
    dec     eax
    cmp     [idx], eax
    jge     .done
    lea     edx, [ch]
    invoke  recv, [sock], edx, 1, 0
    cmp     eax, 0
    jle     .eof
    mov     al, [ch]
    cmp     al, 10
    je      .done
    mov     edx, [buf]
    add     edx, [idx]
    mov     [edx], al
    inc     dword [idx]
    jmp     .loop
.eof:
    cmp     dword [idx], 0
    jnz     .done
    mov     eax, -1
    ret
.done:
    mov     edx, [buf]
    add     edx, [idx]
    mov     byte [edx], 0
    mov     eax, [idx]
    ret
endp

proc send_frame sock, msg_type, json_str
    locals
        jlen dd 0
        hdr  db 5 dup (0)
        ok   dd 0
    endl
    cinvoke strlen, [json_str]
    mov     [jlen], eax

    mov     byte [hdr], 'R'
    mov     byte [hdr+1], 'N'
    mov     al, byte [msg_type]
    mov     byte [hdr+2], al
    mov     eax, [jlen]
    mov     cl, ah
    mov     byte [hdr+3], cl
    mov     byte [hdr+4], al

    invoke  EnterCriticalSection, cs_ctrl
    lea     eax, [hdr]
    stdcall send_all, [sock], eax, 5
    test    eax, eax
    jz      .fail
    cmp     dword [jlen], 0
    jz      .ok
    stdcall send_all, [sock], [json_str], [jlen]
    test    eax, eax
    jz      .fail
.ok:
    mov     dword [ok], 1
.fail:
    invoke  LeaveCriticalSection, cs_ctrl
    mov     eax, [ok]
    ret
endp

proc recv_frame sock, out_type, out_buf, max_cap
    locals
        hdr  db 5 dup (0)
        plen dd 0
    endl
    lea     eax, [hdr]
    stdcall recv_exact, [sock], eax, 5
    test    eax, eax
    jz      .fail
    cmp     byte [hdr], 'R'
    jne     .fail
    cmp     byte [hdr+1], 'N'
    jne     .fail

    movzx   eax, byte [hdr+2]
    mov     edx, [out_type]
    mov     [edx], eax

    movzx   eax, byte [hdr+3]
    shl     eax, 8
    movzx   ecx, byte [hdr+4]
    or      eax, ecx
    mov     [plen], eax

    cmp     eax, [max_cap]
    jge     .fail
    test    eax, eax
    jz      .zero_len

    stdcall recv_exact, [sock], [out_buf], [plen]
    test    eax, eax
    jz      .fail

.zero_len:
    mov     edx, [out_buf]
    add     edx, [plen]
    mov     byte [edx], 0
    mov     eax, [plen]
    ret

.fail:
    mov     eax, -1
    ret
endp

; ==============================================================================
; JSON Helpers (Flat Object Extraction & Escaping)
; ==============================================================================
proc json_get_str json_buf, key_str, out_buf, max_len
    locals
        pat db 128 dup (0)
        p   dd 0
        cnt dd 0
    endl
    lea     eax, [pat]
    cinvoke sprintf, eax, szFmtJsonPat, [key_str]
    lea     eax, [pat]
    cinvoke strstr, [json_buf], eax
    test    eax, eax
    jz      .not_found

    mov     esi, eax
    lea     eax, [pat]
    cinvoke strlen, eax
    add     esi, eax

    ; Skip spaces until ':'
.find_colon:
    mov     al, [esi]
    test    al, al
    jz      .not_found
    inc     esi
    cmp     al, ':'
    jne     .find_colon

    ; Skip spaces until '"'
.find_quote:
    mov     al, [esi]
    test    al, al
    jz      .not_found
    cmp     al, '"'
    je      .start_copy
    cmp     al, ' '
    je      .skip_ws
    cmp     al, 9
    je      .skip_ws
    jmp     .not_found
.skip_ws:
    inc     esi
    jmp     .find_quote

.start_copy:
    inc     esi
    mov     edi, [out_buf]
    mov     dword [cnt], 0
.copy_loop:
    mov     al, [esi]
    test    al, al
    jz      .done_copy
    cmp     al, '"'
    je      .done_copy
    cmp     al, '\'
    jne     .store_ch
    inc     esi
    mov     al, [esi]
    test    al, al
    jz      .done_copy
.store_ch:
    mov     ecx, [max_len]
    dec     ecx
    cmp     [cnt], ecx
    jge     .skip_inc
    mov     [edi], al
    inc     edi
    inc     dword [cnt]
.skip_inc:
    inc     esi
    jmp     .copy_loop

.done_copy:
    mov     byte [edi], 0
    mov     eax, 1
    ret

.not_found:
    mov     edx, [out_buf]
    mov     byte [edx], 0
    xor     eax, eax
    ret
endp

proc json_escape src, dst, max_len
    mov     esi, [src]
    mov     edi, [dst]
    mov     ecx, [max_len]
    sub     ecx, 2
.loop:
    mov     al, [esi]
    test    al, al
    jz      .done
    cmp     ecx, 1
    jle     .done
    cmp     al, '"'
    je      .esc
    cmp     al, '\'
    je      .esc
    mov     [edi], al
    inc     edi
    dec     ecx
    inc     esi
    jmp     .loop
.esc:
    cmp     ecx, 2
    jle     .done
    mov     byte [edi], '\'
    mov     [edi+1], al
    add     edi, 2
    sub     ecx, 2
    inc     esi
    jmp     .loop
.done:
    mov     byte [edi], 0
    ret
endp

; ==============================================================================
; Windows Registry RDP Port Auto-Detection
; ==============================================================================
proc detect_rdp_port_reg
    locals
        hKey  dd 0
        val   dd 3389
        cb    dd 4
        dtype dd 0
    endl
    lea     eax, [hKey]
    invoke  RegOpenKeyExA, HKEY_LOCAL_MACHINE, szRegRdpPath, 0, KEY_READ_CONST, eax
    test    eax, eax
    jnz     .fallback

    lea     eax, [cb]
    lea     ecx, [val]
    lea     edx, [dtype]
    invoke  RegQueryValueExA, [hKey], szRegPortVal, 0, edx, ecx, eax
    push    eax
    invoke  RegCloseKey, [hKey]
    pop     eax
    test    eax, eax
    jnz     .fallback

    mov     eax, [val]
    cmp     eax, 1
    jl      .fallback
    cmp     eax, 65535
    jg      .fallback
    ret
.fallback:
    mov     eax, 3389
    ret
endp

; ==============================================================================
; CLI & INI Configuration Helpers
; ==============================================================================
proc parse_cli_args
    invoke  GetCommandLineA
    cinvoke strncpy, cli_copy, eax, 2040
    mov     byte [cli_copy+2040], 0

    mov     esi, cli_copy
    mov     dword [argc], 0

.next_tok:
    mov     al, [esi]
    test    al, al
    jz      .done
    cmp     al, ' '
    je      .skip_sp
    cmp     al, 9
    je      .skip_sp

    mov     ebx, [argc]
    cmp     ebx, 63
    jge     .done

    cmp     al, '"'
    je      .quoted

    mov     [argv + ebx*4], esi
    inc     dword [argc]
.unquoted_loop:
    mov     al, [esi]
    test    al, al
    jz      .done
    cmp     al, ' '
    je      .end_tok
    cmp     al, 9
    je      .end_tok
    inc     esi
    jmp     .unquoted_loop

.quoted:
    inc     esi
    mov     [argv + ebx*4], esi
    inc     dword [argc]
.quoted_loop:
    mov     al, [esi]
    test    al, al
    jz      .done
    cmp     al, '"'
    je      .end_tok
    inc     esi
    jmp     .quoted_loop

.end_tok:
    mov     byte [esi], 0
    inc     esi
    jmp     .next_tok

.skip_sp:
    inc     esi
    jmp     .next_tok
.done:
    ret
endp

proc resolve_config_path
    locals
        i dd 1
    endl
    ; Check -config flag first
    mov     dword [i], 1
.cli_chk:
    mov     eax, [i]
    inc     eax
    cmp     eax, [argc]
    jge     .check_cwd
    mov     ebx, [i]
    mov     edx, [argv + ebx*4]
    cinvoke _stricmp, edx, szFlagConfig
    test    eax, eax
    jnz     .next_i
    mov     ebx, [i]
    inc     ebx
    mov     edx, [argv + ebx*4]
    cinvoke strncpy, config_path, edx, 500
    ret
.next_i:
    inc     dword [i]
    jmp     .cli_chk

.check_cwd:
    invoke  GetFileAttributesA, szIniDefault
    cmp     eax, -1
    je      .check_exe_dir
    cinvoke strcpy, config_path, szIniDefault
    ret

.check_exe_dir:
    invoke  GetModuleFileNameA, 0, exe_dir, 500
    cinvoke strrchr, exe_dir, '\'
    test    eax, eax
    jz      .use_default
    mov     byte [eax], 0
    cinvoke sprintf, config_path, szFmtIniAdj, exe_dir, szIniDefault
    ret

.use_default:
    cinvoke strcpy, config_path, szIniDefault
    ret
endp

proc load_ini_config
    invoke  GetFileAttributesA, config_path
    cmp     eax, -1
    je      .no_ini

    invoke  GetPrivateProfileStringA, szSecServer, szKeyAddress, szDefServer, server_full, 255, config_path
    invoke  GetPrivateProfileStringA, szSecNetwork, szKeyNetId, szEmpty, net_buf, 127, config_path
    invoke  GetPrivateProfileStringA, szSecNetwork, szKeyNetKey, szEmpty, key_buf, 127, config_path
    invoke  GetPrivateProfileStringA, szSecNode, szKeyRole, szDefRole, role_buf, 31, config_path
    invoke  GetPrivateProfileStringA, szSecNode, szKeyName, szEmpty, name_buf, 127, config_path
    invoke  GetPrivateProfileStringA, szSecNode, szKeyTarget, szEmpty, target_buf, 127, config_path
    cinvoke _stricmp, target_buf, szAuto
    test    eax, eax
    jnz     .ini_local
    mov     byte [target_buf], 0
.ini_local:
    invoke  GetPrivateProfileIntA, szSecNode, szKeyLocalPort, 33890, config_path
    mov     [local_port], eax

    invoke  GetPrivateProfileStringA, szSecNode, szKeyRdpPort, szAuto, rdp_port_str, 31, config_path
    cinvoke _stricmp, rdp_port_str, szAuto
    test    eax, eax
    jz      .rdp_auto
    cinvoke atoi, rdp_port_str
    mov     [rdp_port], eax
    jmp     .chk_mstsc
.rdp_auto:
    mov     dword [rdp_port], 0

.chk_mstsc:
    invoke  GetPrivateProfileStringA, szSecNode, szKeyMstsc, szEmpty, mstsc_str, 15, config_path
    cinvoke strcmp, mstsc_str, szOne
    test    eax, eax
    jz      .set_mstsc
    cinvoke _stricmp, mstsc_str, szTrue
    test    eax, eax
    jnz     .done
.set_mstsc:
    mov     dword [flag_mstsc], 1
    jmp     .done

.no_ini:
    cinvoke strcpy, server_full, szDefServer
    cinvoke strcpy, role_buf, szDefRole
.done:
    ret
endp

proc apply_cli_overrides
    locals
        i dd 1
    endl
.loop:
    mov     eax, [i]
    cmp     eax, [argc]
    jge     .done

    mov     edx, [argv + eax*4]
    push    edx

    cinvoke _stricmp, edx, szFlagHost
    test    eax, eax
    jnz     .chk_viewer
    cinvoke strcpy, role_buf, szRoleHost
    pop     edx
    jmp     .next

.chk_viewer:
    pop     edx
    push    edx
    cinvoke _stricmp, edx, szFlagViewer
    test    eax, eax
    jnz     .chk_silent
    cinvoke strcpy, role_buf, szRoleViewer
    pop     edx
    jmp     .next

.chk_silent:
    pop     edx
    push    edx
    cinvoke _stricmp, edx, szFlagSilent
    test    eax, eax
    jnz     .chk_mstsc
    mov     dword [flag_silent], 1
    pop     edx
    jmp     .next

.chk_mstsc:
    pop     edx
    push    edx
    cinvoke _stricmp, edx, szFlagMstsc
    test    eax, eax
    jnz     .chk_val_flags
    mov     dword [flag_mstsc], 1
    pop     edx
    jmp     .next

.chk_val_flags:
    pop     edx
    mov     eax, [i]
    inc     eax
    cmp     eax, [argc]
    jge     .next
    mov     ecx, [argv + eax*4]   ; next value arg
    push    ecx
    push    edx

    cinvoke _stricmp, edx, szFlagServer
    test    eax, eax
    jnz     .chk_net
    pop     edx
    pop     ecx
    cinvoke strncpy, server_full, ecx, 250
    inc     dword [i]
    jmp     .next

.chk_net:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagNet
    test    eax, eax
    jnz     .chk_key
    pop     edx
    pop     ecx
    cinvoke strncpy, net_buf, ecx, 120
    inc     dword [i]
    jmp     .next

.chk_key:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagKey
    test    eax, eax
    jnz     .chk_name
    pop     edx
    pop     ecx
    cinvoke strncpy, key_buf, ecx, 120
    inc     dword [i]
    jmp     .next

.chk_name:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagName
    test    eax, eax
    jnz     .chk_target
    pop     edx
    pop     ecx
    cinvoke strncpy, name_buf, ecx, 120
    inc     dword [i]
    jmp     .next

.chk_target:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagTarget
    test    eax, eax
    jnz     .chk_local
    pop     edx
    pop     ecx
    cinvoke strncpy, target_buf, ecx, 120
    cinvoke _stricmp, target_buf, szAuto
    test    eax, eax
    jnz     .target_inc
    mov     byte [target_buf], 0
.target_inc:
    inc     dword [i]
    jmp     .next

.chk_local:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagLocal
    test    eax, eax
    jnz     .chk_rdp
    pop     edx
    pop     ecx
    cinvoke atoi, ecx
    mov     [local_port], eax
    inc     dword [i]
    jmp     .next

.chk_rdp:
    pop     edx
    pop     ecx
    push    ecx
    push    edx
    cinvoke _stricmp, edx, szFlagRdpPort
    test    eax, eax
    jnz     .no_match
    pop     edx
    pop     ecx
    cinvoke atoi, ecx
    mov     [rdp_port], eax
    inc     dword [i]
    jmp     .next

.no_match:
    pop     edx
    pop     ecx
.next:
    inc     dword [i]
    jmp     .loop
.done:
    ret
endp

section '.idata' import data readable writeable

    library kernel32, 'KERNEL32.DLL',\
            advapi32, 'ADVAPI32.DLL',\
            ws2_32,   'WS2_32.DLL',\
            user32,   'USER32.DLL',\
            msvcrt,   'MSVCRT.DLL'

    import kernel32,\
           ExitProcess, 'ExitProcess',\
           GetCommandLineA, 'GetCommandLineA',\
           GetPrivateProfileStringA, 'GetPrivateProfileStringA',\
           GetPrivateProfileIntA, 'GetPrivateProfileIntA',\
           GetComputerNameA, 'GetComputerNameA',\
           GetModuleFileNameA, 'GetModuleFileNameA',\
           GetFileAttributesA, 'GetFileAttributesA',\
           GetConsoleWindow, 'GetConsoleWindow',\
           InitializeCriticalSection, 'InitializeCriticalSection',\
           EnterCriticalSection, 'EnterCriticalSection',\
           LeaveCriticalSection, 'LeaveCriticalSection',\
           CreateEventA, 'CreateEventA',\
           ResetEvent, 'ResetEvent',\
           SetEvent, 'SetEvent',\
           WaitForSingleObject, 'WaitForSingleObject',\
           CreateThread, 'CreateThread',\
           CloseHandle, 'CloseHandle',\
           Sleep, 'Sleep',\
           WinExec, 'WinExec'

    import advapi32,\
           RegOpenKeyExA, 'RegOpenKeyExA',\
           RegQueryValueExA, 'RegQueryValueExA',\
           RegCloseKey, 'RegCloseKey'

    import ws2_32,\
           WSAStartup, 'WSAStartup',\
           WSACleanup, 'WSACleanup',\
           socket, 'socket',\
           connect, 'connect',\
           bind, 'bind',\
           listen, 'listen',\
           accept, 'accept',\
           send, 'send',\
           recv, 'recv',\
           shutdown, 'shutdown',\
           closesocket, 'closesocket',\
           setsockopt, 'setsockopt',\
           htons, 'htons',\
           inet_addr, 'inet_addr',\
           gethostbyname, 'gethostbyname'

    import user32,\
           ShowWindow, 'ShowWindow'

    import msvcrt,\
           printf, 'printf',\
           sprintf, 'sprintf',\
           fflush, 'fflush',\
           atoi, 'atoi',\
           strlen, 'strlen',\
           strcpy, 'strcpy',\
           strncpy, 'strncpy',\
           strcmp, 'strcmp',\
           _stricmp, '_stricmp',\
           strchr, 'strchr',\
           strrchr, 'strrchr',\
           strstr, 'strstr',\
           malloc, 'malloc',\
           free, 'free'
