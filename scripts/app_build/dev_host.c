/* Source-only macOS host: retain bundle identity without copying Python.
 * The generator supplies the exact, non-resolved venv executable as a C string.
 * Py_BytesMain owns initialization, stdin, signals and the final exit status.
 */
extern int Py_BytesMain(int argc, char **argv);

int main(int argc, char **argv) {
    argv[0] = SCOPECAT_DEV_PYTHON;
    return Py_BytesMain(argc, argv);
}
