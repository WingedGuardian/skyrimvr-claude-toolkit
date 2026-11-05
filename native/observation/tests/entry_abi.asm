.code
; Synthetic Windows x64 caller. The production forwarding template must preserve
; its caller's nonvolatile registers and XMM6, and supply normal shadow space.
EntryAbi PROC FRAME
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
    sub rsp,58h
    .allocstack 58h
    movdqu [rsp+20h],xmm6
    .savexmm128 xmm6,20h
    .endprolog
    mov rax,rcx
    mov rcx,rdx
    mov rbx,11223344h
    mov rbp,22334455h
    mov rsi,33445566h
    mov rdi,44556677h
    mov r12,55667788h
    mov r13,66778899h
    mov r14,778899AAh
    mov r15,7A8899AAh
    movd xmm6,ebx
    call rax
    cmp rbx,11223344h
    jne bad
    cmp rbp,22334455h
    jne bad
    cmp rsi,33445566h
    jne bad
    cmp rdi,44556677h
    jne bad
    cmp r12,55667788h
    jne bad
    cmp r13,66778899h
    jne bad
    cmp r14,778899AAh
    jne bad
    cmp r15,7A8899AAh
    jne bad
    movd eax,xmm6
    cmp eax,11223344h
    jne bad
    mov eax,1
    jmp done
bad:
    xor eax,eax
done:
    movdqu xmm6,[rsp+20h]
    add rsp,58h
    pop r15
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbp
    pop rbx
    ret
EntryAbi ENDP
END
