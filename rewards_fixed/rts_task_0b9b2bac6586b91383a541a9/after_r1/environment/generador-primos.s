.global _start

.data
// Static strings
msg_intro:
    .ascii "N\xc3\xbameros primos hasta "
msg_intro_len = . - msg_intro

msg_colon:
    .ascii ":\n"
msg_colon_len = . - msg_colon

space:
    .ascii " "
space_len = . - space

newline:
    .ascii "\n"
newline_len = . - newline

config_path:
    .asciz "/root/config.txt"

// Buffers
filebuf:
    .skip 64

num_buf:
    .skip 20

.text
_start:
    // Open /root/config.txt for reading
    ldr x0, =-100             // AT_FDCWD
    adr x1, config_path
    mov x2, #0               // O_RDONLY
    mov x8, #56              // __NR_openat
    svc #0
    mov x20, x0              // save file descriptor

    cmp x0, #0
    b.lt exit_silent          // if open fails, exit silently

    // Read up to 64 bytes from config file
    mov x0, x20
    adr x1, filebuf
    mov x2, #64
    mov x8, #63              // __NR_read
    svc #0
    mov x21, x0              // bytes read

    // Close file descriptor
    mov x0, x20
    mov x8, #57              // __NR_close
    svc #0

    // Parse integer from filebuf (x21 bytes)
    mov w19, #0              // limit = 0
    adr x22, filebuf
    mov x23, #0              // index

parse_loop:
    cmp x23, x21
    b.eq parse_done
    ldrb w24, [x22, x23]     // load byte
    sub w24, w24, #'0'       // convert to digit value
    cmp w24, #9
    b.hi parse_done          // non-digit ends parsing
    mov w25, #10
    mul w19, w19, w25
    add w19, w19, w24
    add x23, x23, #1
    b parse_loop

parse_done:
    // w19 now contains the limit read from config

    // Print header "Números primos hasta "
    mov x0, #1
    adr x1, msg_intro
    mov x2, #msg_intro_len
    mov x8, #64
    svc #0

    // Convert limit to ASCII and print it
    mov x24, x19
    bl num_to_ascii

    // Print colon and newline
    mov x0, #1
    adr x1, msg_colon
    mov x2, #msg_colon_len
    mov x8, #64
    svc #0

    // TODO: Generate and print every prime from 2 through w19.
    // Use num_to_ascii to print a number and print_space between numbers.

    // Print final newline
    mov x0, #1
    adr x1, newline
    mov x2, #newline_len
    mov x8, #64
    svc #0

    mov x0, #0
    mov x8, #93              // __NR_exit
    svc #0

exit_silent:
    mov x0, #1
    mov x8, #93
    svc #0

print_space:
    mov x0, #1
    adr x1, space
    mov x2, #space_len
    mov x8, #64
    svc #0
    ret

// Subroutine: num_to_ascii
// Input:  x24 = unsigned integer to print
// Prints the number to stdout via syscall write
num_to_ascii:
    mov x25, x24
    adr x26, num_buf
    add x26, x26, #19        // point to end of buffer
    mov x27, #10

    cbnz x25, convert_loop
    mov w28, #'0'
    strb w28, [x26]
    sub x26, x26, #1
    b end_convert

convert_loop:
    cbz x25, end_convert
    udiv x28, x25, x27
    msub x29, x28, x27, x25  // digit = x25 % 10
    add w29, w29, #'0'
    strb w29, [x26]
    sub x26, x26, #1
    mov x25, x28
    b convert_loop

end_convert:
    add x26, x26, #1          // x26 points to first digit
    adr x25, num_buf
    add x25, x25, #20
    sub x2, x25, x26          // length

    mov x0, #1
    mov x1, x26
    mov x8, #64
    svc #0

    ret
