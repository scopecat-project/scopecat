/* Source host: Python stays in its editable environment; the window belongs to us. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shobjidl.h>
#include <stdio.h>
#include "host_config.h"

typedef int (__cdecl *PythonMain)(int, wchar_t **);

int wmain(int argc, wchar_t **argv) {
    HMODULE runtime = LoadLibraryExW(SCOPECAT_DEV_LIBRARY, NULL,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    PythonMain python_main = runtime ? (PythonMain)GetProcAddress(runtime, "Py_Main") : NULL;
    if (!python_main || FAILED(SetCurrentProcessExplicitAppUserModelID(SCOPECAT_DEV_ID))) {
        fwprintf(stderr, L"Cannot initialize the Scopecat development host.\n");
        return 1;
    }
    argv[0] = SCOPECAT_DEV_PYTHON;
    return python_main(argc, argv);
}
