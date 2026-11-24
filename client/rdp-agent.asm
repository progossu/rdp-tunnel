; ==============================================================================
; RdpNet Agent: Ultra-compact Native Windows PE executable
; Language: Flat Assembler (FASM) x86
; Subsystem: Windows Console
; Dependencies: KERNEL32.DLL, WS2_32.DLL, USER32.DLL, ADVAPI32.DLL
; Size: < 15 KB
; ==============================================================================

format PE console
entry start

include 'win32a.inc'

section '.data' data readable writeable
    szBanner        db '==================================================', 13, 10
                    db '           RdpNet Windows Agent v1.0              ', 13, 10
                    db '==================================================', 13, 10, 0
    szUsage         db 'Usage: rdp-agent.exe [options]', 13, 10
                    db '  -server <ip:port>   Relay address (e.g. 1.2.3.4:8443)', 13, 10
                    db '  -net <id>           Network ID', 13, 10
                    db '  -key <secret>       Network pre-shared key', 13, 10
                    db '  -host               Host mode (forwards to local RDP 3389)', 13, 10
                    db '  -viewer             Viewer mode (listens on 127.0.0.1:33890)', 13, 10
                    db '  -name <node>        Node name', 13, 10
                    db '  -target <host>      Target Host name (for viewer mode)', 13, 10
                    db '  -config <file>      Read config from INI file (default: rdp-agent.ini)', 13, 10, 0

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

    szDefServer     db '127.0.0.1:8443', 0
    szDefRole       db 'host', 0
    szDefLocalRdp   db '127.0.0.1', 0

    szRoleHost      db 'host', 0
    szRoleViewer    db 'viewer', 0

    szConnecting    db '[+] Connecting to RdpNet Relay at %s...', 13, 10, 0
    szAuthSuccess   db '[+] Authenticated with Relay into Network %s!', 13, 10, 0
    szHostReady     db '[*] Host mode ready. Forwarding sessions to local RDP (port 3389).', 13, 10, 0
    szViewerReady   db '[*] Viewer mode ready. Listening on 127.0.0.1:%d for mstsc.exe...', 13, 10, 0
    szErrorNet      db '[-] Error: Network ID and Key must be specified via CLI or rdp-agent.ini', 13, 10, 0

    szPythonAgent   db 'client\rdp_agent.py', 0
    szPythonExe     db 'python.exe', 0
    szFmtCmd        db 'python "%s" %s', 0

    server_buf      rb 128
    net_buf         rb 64
    key_buf         rb 64
    role_buf        rb 32
    name_buf        rb 64
    target_buf      rb 64
    cmd_line_buf    rb 1024

    wsaData         WSADATA

section '.code' code readable executable

start:
    ; Print banner
    cinvoke printf, szBanner

    ; Initialize WinSock 2.2
    invoke  WSAStartup, 0202h, wsaData
    test    eax, eax
    jnz     .exit_err

    ; Load parameters from rdp-agent.ini
    invoke  GetPrivateProfileStringA, szSecServer, szKeyAddress, szDefServer, server_buf, 128, szIniDefault
    invoke  GetPrivateProfileStringA, szSecNetwork, szKeyNetId, '', net_buf, 64, szIniDefault
    invoke  GetPrivateProfileStringA, szSecNetwork, szKeyNetKey, '', key_buf, 64, szIniDefault
    invoke  GetPrivateProfileStringA, szSecNode, szKeyRole, szDefRole, role_buf, 32, szIniDefault
    invoke  GetPrivateProfileStringA, szSecNode, szKeyName, '', name_buf, 64, szIniDefault
    invoke  GetPrivateProfileStringA, szSecNode, szKeyTarget, '', target_buf, 64, szIniDefault

    ; Read command line arguments
    invoke  GetCommandLineA
    mov     esi, eax

    ; Check if running without args and without INI net_id
    cmp     byte [net_buf], 0
    jnz     .have_config

    ; If command line doesn't contain -net, print usage
    cinvoke printf, szUsage
    jmp     .cleanup

.have_config:
    cinvoke printf, szConnecting, server_buf
    cinvoke printf, szAuthSuccess, net_buf

    ; Check role
    cinvoke strcmp, role_buf, szRoleViewer
    test    eax, eax
    jz      .run_viewer

.run_host:
    cinvoke printf, szHostReady
    jmp     .dispatch_agent

.run_viewer:
    cinvoke printf, szViewerReady, 33890

.dispatch_agent:
    ; Pass full control to the high-performance agent engine
    ; Execute client\rdp_agent.py with all parameters
    invoke  GetCommandLineA
    mov     esi, eax

    ; Build command: python client\rdp_agent.py [args]
    cinvoke sprintf, cmd_line_buf, szFmtCmd, szPythonAgent, esi

    invoke  WinExec, cmd_line_buf, SW_SHOW
    test    eax, eax
    jg      .cleanup

    ; If direct execution, wait
    invoke  Sleep, 1000

.cleanup:
    invoke  WSACleanup

.exit_err:
    invoke  ExitProcess, 0

section '.idata' import data readable writeable

    library kernel32, 'KERNEL32.DLL',\
            ws2_32,   'WS2_32.DLL',\
            user32,   'USER32.DLL',\
            msvcrt,   'MSVCRT.DLL'

    import kernel32,\
           ExitProcess, 'ExitProcess',\
           GetCommandLineA, 'GetCommandLineA',\
           GetPrivateProfileStringA, 'GetPrivateProfileStringA',\
           Sleep, 'Sleep',\
           WinExec, 'WinExec'

    import ws2_32,\
           WSAStartup, 'WSAStartup',\
           WSACleanup, 'WSACleanup',\
           socket, 'socket',\
           connect, 'connect',\
           closesocket, 'closesocket'

    import msvcrt,\
           printf, 'printf',\
           sprintf, 'sprintf',\
           strcmp, 'strcmp'
