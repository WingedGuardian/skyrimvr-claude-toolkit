OPTION CASEMAP:NONE
EXTERN SkyrimNativeTestStateMask:QWORD
EXTERN SkyrimNativeObserveImpl:PROC
.code
; Test-only forwarding shim deliberately uses all four incoming home slots.
; The production bridge must reserve those 32 bytes without overlapping Context.
; Distinct XMM6..15, so the bridge's unwind slots can be told apart. Arguments: target, RCX argument.
SkyrimNativeDistinctVectors PROC FRAME
    sub rsp,0C8h
    .allocstack 0C8h
    movdqu [rsp+20h],xmm6
    .savexmm128 xmm6,20h
    movdqu [rsp+30h],xmm7
    .savexmm128 xmm7,30h
    movdqu [rsp+40h],xmm8
    .savexmm128 xmm8,40h
    movdqu [rsp+50h],xmm9
    .savexmm128 xmm9,50h
    movdqu [rsp+60h],xmm10
    .savexmm128 xmm10,60h
    movdqu [rsp+70h],xmm11
    .savexmm128 xmm11,70h
    movdqu [rsp+80h],xmm12
    .savexmm128 xmm12,80h
    movdqu [rsp+90h],xmm13
    .savexmm128 xmm13,90h
    movdqu [rsp+0A0h],xmm14
    .savexmm128 xmm14,0A0h
    movdqu [rsp+0B0h],xmm15
    .savexmm128 xmm15,0B0h
    .endprolog
    mov rax,rcx
    mov rcx,rdx
    mov r8d,61000000h
    movd xmm6,r8d
    mov r8d,61000001h
    movd xmm7,r8d
    mov r8d,61000002h
    movd xmm8,r8d
    mov r8d,61000003h
    movd xmm9,r8d
    mov r8d,61000004h
    movd xmm10,r8d
    mov r8d,61000005h
    movd xmm11,r8d
    mov r8d,61000006h
    movd xmm12,r8d
    mov r8d,61000007h
    movd xmm13,r8d
    mov r8d,61000008h
    movd xmm14,r8d
    mov r8d,61000009h
    movd xmm15,r8d
    call rax
    movdqu xmm6,[rsp+20h]
    movdqu xmm7,[rsp+30h]
    movdqu xmm8,[rsp+40h]
    movdqu xmm9,[rsp+50h]
    movdqu xmm10,[rsp+60h]
    movdqu xmm11,[rsp+70h]
    movdqu xmm12,[rsp+80h]
    movdqu xmm13,[rsp+90h]
    movdqu xmm14,[rsp+0A0h]
    movdqu xmm15,[rsp+0B0h]
    add rsp,0C8h
    ret
SkyrimNativeDistinctVectors ENDP
SkyrimNativeObserve PROC
    mov QWORD PTR [rsp+8],11223344h
    mov QWORD PTR [rsp+10h],22334455h
    mov QWORD PTR [rsp+18h],33445566h
    mov QWORD PTR [rsp+20h],44556677h
    jmp SkyrimNativeObserveImpl
SkyrimNativeObserve ENDP
; Test caller owns a fixed stack frame and seeds every GPR except RSP, every
; XMM register and arithmetic flags. Output is captured BEFORE any comparison.
; Arguments: target, RCX argument, output Context. No game code is executed.
SkyrimNativeCapture PROC FRAME
    push rbx
    .pushreg rbx
    push rbp
    .pushreg rbp
    push rsi
    .pushreg rsi
    push rdi
    .pushreg rdi
    push r12
    .pushreg r12
    push r13
    .pushreg r13
    push r14
    .pushreg r14
    push r15
    .pushreg r15
    sub rsp, 388h
    .allocstack 388h
    movdqu [rsp+20h],xmm6
    .savexmm128 xmm6,20h
    movdqu [rsp+30h],xmm7
    .savexmm128 xmm7,30h
    movdqu [rsp+40h],xmm8
    .savexmm128 xmm8,40h
    movdqu [rsp+50h],xmm9
    .savexmm128 xmm9,50h
    movdqu [rsp+60h],xmm10
    .savexmm128 xmm10,60h
    movdqu [rsp+70h],xmm11
    .savexmm128 xmm11,70h
    movdqu [rsp+80h],xmm12
    .savexmm128 xmm12,80h
    movdqu [rsp+90h],xmm13
    .savexmm128 xmm13,90h
    movdqu [rsp+0A0h],xmm14
    .savexmm128 xmm14,0A0h
    movdqu [rsp+0B0h],xmm15
    .savexmm128 xmm15,0B0h
    .endprolog
    mov [rsp+0C0h],rcx
    mov [rsp+0C8h],r8
    mov rcx,rdx
    mov rax,10101010h
    mov rdx,20202020h
    mov rbx,30303030h
    mov rbp,50505050h
    mov rsi,60606060h
    mov rdi,70707071h
    mov r8,80808080h
    mov r9,90909090h
    mov r10,0A0A0A0A0h
    mov r11,0B0B0B0B0h
    mov r12,0C0C0C0C0h
    mov r13,0D0D0D0D0h
    mov r14,0E0E0E0E0h
    mov r15,0F0F0F0F0h
    pcmpeqd xmm0,xmm0
    movdqa xmm1,xmm0
    movdqa xmm2,xmm0
    movdqa xmm3,xmm0
    movdqa xmm4,xmm0
    movdqa xmm5,xmm0
    movdqa xmm6,xmm0
    movdqa xmm7,xmm0
    movdqa xmm8,xmm0
    movdqa xmm9,xmm0
    movdqa xmm10,xmm0
    movdqa xmm11,xmm0
    movdqa xmm12,xmm0
    movdqa xmm13,xmm0
    movdqa xmm14,xmm0
    movdqa xmm15,xmm0
    test QWORD PTR SkyrimNativeTestStateMask,4
    jz seeded
    vpcmpeqd ymm0,ymm0,ymm0
    vmovdqa ymm1,ymm0
    vmovdqa ymm2,ymm0
    vmovdqa ymm3,ymm0
    vmovdqa ymm4,ymm0
    vmovdqa ymm5,ymm0
    vmovdqa ymm6,ymm0
    vmovdqa ymm7,ymm0
    vmovdqa ymm8,ymm0
    vmovdqa ymm9,ymm0
    vmovdqa ymm10,ymm0
    vmovdqa ymm11,ymm0
    vmovdqa ymm12,ymm0
    vmovdqa ymm13,ymm0
    vmovdqa ymm14,ymm0
    vmovdqa ymm15,ymm0
seeded:
    ; CF=1 is deliberately preserved by INC, unlike ADD or ordinary observer.
    stc
    call QWORD PTR [rsp+0C0h]
    ; Snapshot into stack storage while retaining all returned registers.
    mov [rsp+0D0h],rax
    mov [rsp+0D8h],rcx
    mov [rsp+0E0h],rdx
    mov [rsp+0E8h],rbx
    lea rax,[rsp]
    mov [rsp+0F0h],rax
    mov [rsp+0F8h],rbp
    mov [rsp+100h],rsi
    mov [rsp+108h],rdi
    mov [rsp+110h],r8
    mov [rsp+118h],r9
    mov [rsp+120h],r10
    mov [rsp+128h],r11
    mov [rsp+130h],r12
    mov [rsp+138h],r13
    mov [rsp+140h],r14
    mov [rsp+148h],r15
    pushfq
    pop QWORD PTR [rsp+150h]
    mov QWORD PTR [rsp+158h],0
    ; All returned GPRs/flags are saved before obtaining the external buffer.
    mov rdi,[rsp+0C8h]
    mov rdi,[rdi+90h]
    mov eax,DWORD PTR SkyrimNativeTestStateMask
    mov edx,DWORD PTR SkyrimNativeTestStateMask+4
    xsave64 [rdi]
    lea rsi,[rsp+0D0h]
    mov rdi,[rsp+0C8h]
    mov ecx,12h
    rep movsq
    movdqu xmm6,[rsp+20h]
    movdqu xmm7,[rsp+30h]
    movdqu xmm8,[rsp+40h]
    movdqu xmm9,[rsp+50h]
    movdqu xmm10,[rsp+60h]
    movdqu xmm11,[rsp+70h]
    movdqu xmm12,[rsp+80h]
    movdqu xmm13,[rsp+90h]
    movdqu xmm14,[rsp+0A0h]
    movdqu xmm15,[rsp+0B0h]
    add rsp,388h
    pop r15
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbp
    pop rbx
    ret
SkyrimNativeCapture ENDP
SkyrimNativeLeaf PROC
    inc DWORD PTR [rcx+78h]
    ret
SkyrimNativeLeaf ENDP
SkyrimNativeNoOpLeaf PROC
    ret
    int 3
    int 3
    int 3
SkyrimNativeNoOpLeaf ENDP
SkyrimNativeMutatingLeaf PROC
    inc DWORD PTR [rcx+78h]
    mov rax,11112222h
    mov rdx,22223333h
    mov r8,33334444h
    mov r9,44445555h
    mov r10,55556666h
    mov r11,66667777h
    pxor xmm0,xmm0
    clc
    ret
SkyrimNativeMutatingLeaf ENDP
; Deliberate volatile extended-state damage, independently of the bridge mask.
; Lower halves of nonvolatile XMM registers are left intact.
SkyrimNativeClobberExtended PROC FRAME
    sub rsp,28h
    .allocstack 28h
    .endprolog
    stmxcsr [rsp+20h]
    mov eax,[rsp+20h]
    xor eax,6000h
    mov [rsp+24h],eax
    ldmxcsr [rsp+24h]
    fnstcw [rsp+20h]
    mov ax,[rsp+20h]
    xor ax,0C00h
    mov [rsp+22h],ax
    fldcw [rsp+22h]
    test QWORD PTR SkyrimNativeTestStateMask,4
    jz noavx
    vzeroupper
noavx:
    xor eax,eax
    pxor xmm0,xmm0
    pxor xmm1,xmm1
    pxor xmm2,xmm2
    pxor xmm3,xmm3
    pxor xmm4,xmm4
    pxor xmm5,xmm5
    add rsp,28h
    ret
SkyrimNativeClobberExtended ENDP
END
