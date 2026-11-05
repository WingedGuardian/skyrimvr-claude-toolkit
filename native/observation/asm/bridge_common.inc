OPTION CASEMAP:NONE
EXTERN __chkstk:PROC
EXTERN __imp_GetLastError:QWORD
EXTERN __imp_SetLastError:QWORD
EXTERN SkyrimNativeObserve:PROC
EXTERN SkyrimNativeXStateMask:QWORD

; Fixed frame, all offsets from post-prologue RSP:
;   20..BF Context, C0..15F original nonvolatile XMM unwind copies,
;   align_up(RSP+180,64)..+4000 aligned XSAVE storage.
;   4200 R11, 4208 R10, 4210 RAX, 4218 flags, 4220 RBP, 4228 caller.
; RBP remains the unwind frame pointer across calls. Bind only body/API-qualified
; originals that do not consume incoming RBP; restore the caller's RBP on return.
; Stack arguments are NOT forwarded: the original's fifth argument would read this
; frame. Bind only sites with 4 or fewer register arguments and no varargs.
; The observer must be noexcept and must not fault: an escaping exception in phase 0
; unwinds through here and the original never runs. The packet is reloaded into the
; registers afterwards, so a passive observer must treat it as read-only.
; Each call uses about 17 KB of stack.

SAVE_XSTATE MACRO
    mov r10,[rsp+0B0h]
    mov QWORD PTR [r10+200h],0
    mov QWORD PTR [r10+208h],0
    mov QWORD PTR [r10+210h],0
    mov QWORD PTR [r10+218h],0
    mov QWORD PTR [r10+220h],0
    mov QWORD PTR [r10+228h],0
    mov QWORD PTR [r10+230h],0
    mov QWORD PTR [r10+238h],0
    mov eax,DWORD PTR SkyrimNativeXStateMask
    mov edx,DWORD PTR SkyrimNativeXStateMask+4
    xsave64 [r10]
ENDM

RESTORE_CONTEXT MACRO
    mov r10,[rsp+0B0h]
    mov eax,DWORD PTR SkyrimNativeXStateMask
    mov edx,DWORD PTR SkyrimNativeXStateMask+4
    xrstor64 [r10]
    push QWORD PTR [rsp+0A0h]
    popfq
    mov rcx,[rsp+28h]
    mov rdx,[rsp+30h]
    mov rbx,[rsp+38h]
    mov rsi,[rsp+50h]
    mov rdi,[rsp+58h]
    mov r8,[rsp+60h]
    mov r9,[rsp+68h]
    mov r10,[rsp+70h]
    mov r11,[rsp+78h]
    mov r12,[rsp+80h]
    mov r13,[rsp+88h]
    mov r14,[rsp+90h]
    mov r15,[rsp+98h]
    mov rax,[rsp+20h]
ENDM

OBSERVE MACRO site,phase
    call QWORD PTR __imp_GetLastError
    mov [rsp+0B8h],eax
    mov ecx,site
    mov edx,phase
    lea r8,[rsp+20h]
    cld
    call SkyrimNativeObserve
    mov ecx,[rsp+0B8h]
    call QWORD PTR __imp_SetLastError
ENDM

BRIDGE MACRO name,original,site
name PROC FRAME
    push rbp
    .pushreg rbp
    pushfq
    .allocstack 8
    push rax
    .allocstack 8
    push r10
    .allocstack 8
    push r11
    .allocstack 8
    ; __chkstk preserves argument registers; its RAX/R10/R11/flags effects
    ; are covered by the preceding saves. It needs no ordinary shadow area.
    mov eax,4200h
    call __chkstk
    sub rsp,rax
    .allocstack 4200h
    lea rbp,[rsp+80h]
    .setframe rbp,80h
    mov [rsp+38h],rbx
    .savereg rbx,38h
    mov [rsp+50h],rsi
    .savereg rsi,50h
    mov [rsp+58h],rdi
    .savereg rdi,58h
    mov [rsp+80h],r12
    .savereg r12,80h
    mov [rsp+88h],r13
    .savereg r13,88h
    mov [rsp+90h],r14
    .savereg r14,90h
    mov [rsp+98h],r15
    .savereg r15,98h
    movdqu [rsp+0C0h],xmm6
    .savexmm128 xmm6,0C0h
    movdqu [rsp+0D0h],xmm7
    .savexmm128 xmm7,0D0h
    movdqu [rsp+0E0h],xmm8
    .savexmm128 xmm8,0E0h
    movdqu [rsp+0F0h],xmm9
    .savexmm128 xmm9,0F0h
    movdqu [rsp+100h],xmm10
    .savexmm128 xmm10,100h
    movdqu [rsp+110h],xmm11
    .savexmm128 xmm11,110h
    movdqu [rsp+120h],xmm12
    .savexmm128 xmm12,120h
    movdqu [rsp+130h],xmm13
    .savexmm128 xmm13,130h
    movdqu [rsp+140h],xmm14
    .savexmm128 xmm14,140h
    movdqu [rsp+150h],xmm15
    .savexmm128 xmm15,150h
    .endprolog
    mov [rsp+28h],rcx
    mov [rsp+30h],rdx
    mov [rsp+60h],r8
    mov [rsp+68h],r9
    mov rax,[rsp+4210h]
    mov [rsp+20h],rax
    mov rax,[rsp+4208h]
    mov [rsp+70h],rax
    mov rax,[rsp+4200h]
    mov [rsp+78h],rax
    mov rax,[rsp+4220h]
    mov [rsp+48h],rax
    lea rax,[rsp+4228h]
    mov [rsp+40h],rax
    mov rax,[rsp+4218h]
    mov [rsp+0A0h],rax
    mov rax,[rsp+4228h]
    mov [rsp+0A8h],rax
    lea rax,[rsp+1BFh]
    and rax,-64
    mov [rsp+0B0h],rax
    mov DWORD PTR [rsp+0BCh],0
    SAVE_XSTATE
    OBSERVE site,0
    RESTORE_CONTEXT
    ; Exactly one original invocation. There is no catch/handler around it.
    call QWORD PTR original
    ; Capture the original's return state BEFORE any arithmetic or helper call.
    pushfq
    pop QWORD PTR [rsp+0A0h]
    mov [rsp+20h],rax
    mov [rsp+28h],rcx
    mov [rsp+30h],rdx
    mov [rsp+38h],rbx
    mov [rsp+50h],rsi
    mov [rsp+58h],rdi
    mov [rsp+60h],r8
    mov [rsp+68h],r9
    mov [rsp+70h],r10
    mov [rsp+78h],r11
    mov [rsp+80h],r12
    mov [rsp+88h],r13
    mov [rsp+90h],r14
    mov [rsp+98h],r15
    SAVE_XSTATE
    OBSERVE site,1
    RESTORE_CONTEXT
    ; Legal frame-pointer epilogue; no flag-changing ADD after POPFQ.
    lea rsp,[rbp+41A0h]
    pop rbp
    ret
name ENDP
ENDM

