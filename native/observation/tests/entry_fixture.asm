EXTERN EntryBody:PROC
.code
; Authored fixtures. Each has the displaced shape of one descriptor the tests exercise; none is game code.
; Shape A: REX-padded PUSH RSI, PUSH RDI, PUSH R14.
EntryFrame PROC FRAME
    db 40h
    push rsi
    .pushreg rsi
    push rdi
    .pushreg rdi
    push r14
    .pushreg r14
    sub rsp,30h
    .allocstack 30h
    .endprolog
    call EntryBody
    add rsp,30h
    pop r14
    pop rdi
    pop rsi
    ret
    db 16 dup(90h)
EntryFrame ENDP
; Shape B: REX-padded PUSH RBX, PUSH RBP, PUSH R14. Used through unwind only.
EntryFrameB PROC FRAME
    db 40h
    push rbx
    .pushreg rbx
    push rbp
    .pushreg rbp
    push r14
    .pushreg r14
    sub rsp,30h
    .allocstack 30h
    .endprolog
    add rsp,30h
    pop r14
    pop rbp
    pop rbx
    ret
    db 24 dup(90h)
EntryFrameB ENDP
; Shape C: MOV [RSP+20h],R9, with no unwind operation. Used through unwind only.
EntryFrameC PROC
    mov [rsp+20h],r9
    ret
    db 32 dup(90h)
EntryFrameC ENDP
END
