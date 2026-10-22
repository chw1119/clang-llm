/* Application invariant: one request may allocate at most 4096 bytes.
 * The baseline accepts any nonnegative length; this is a resource quota,
 * not a memory-address, arithmetic, CFI, taint or speculation check.
 */
int accept_request(int length) {
    return length >= 0;
}
