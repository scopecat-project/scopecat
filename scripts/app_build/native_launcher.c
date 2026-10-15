#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <shobjidl.h>
#include <wchar.h>
#include <stdlib.h>

typedef int (__cdecl *PythonMain)(int, wchar_t **);

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show) {
    int argc;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 1;
    BOOL check = FALSE;
    for (int i = 1; i < argc; ++i)
        if (!wcscmp(argv[i], L"--check-result")) check = TRUE;
    int result = 1;
    wchar_t module[32768];
    DWORD length = GetModuleFileNameW(NULL, module, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *slash = wcsrchr(module, L'\\');
    if (!slash) return 1;
    *slash = L'\0';
    size_t capacity = wcslen(module) + 64;
    wchar_t *python = calloc(capacity, sizeof(wchar_t));
    wchar_t *library = calloc(capacity, sizeof(wchar_t));
    wchar_t *script = calloc(capacity, sizeof(wchar_t));
    wchar_t **arguments = calloc((size_t)argc + 4, sizeof(wchar_t *));
    if (python && library && script && arguments) {
        swprintf_s(python, capacity, L"%ls\\resources\\python\\python.exe", module);
        swprintf_s(library, capacity, L"%ls\\resources\\python\\python3.dll", module);
        swprintf_s(script, capacity, L"%ls\\resources\\bootstrap.py", module);
        // Load only the bundled runtime and its dependencies. Keep the DLL loaded
        // until process exit: extension libraries can retain Python references.
        HMODULE runtime = LoadLibraryExW(library, NULL,
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        PythonMain python_main = runtime ? (PythonMain)GetProcAddress(runtime, "Py_Main") : NULL;
        if (python_main && SUCCEEDED(SetCurrentProcessExplicitAppUserModelID(L"org.scopecat.desktop"))) {
            // The GUI remains in Scopecat.exe. argv[0] selects the ordinary bundled
            // interpreter for sys.executable and backend/author subprocesses.
            arguments[0] = python;
            arguments[1] = L"-I";
            arguments[2] = L"-B";
            arguments[3] = script;
            for (int i = 1; i < argc; ++i) arguments[i + 3] = argv[i];
            result = python_main(argc + 3, arguments);
        }
    }
    free(arguments);
    free(script);
    free(library);
    free(python);
    LocalFree(argv);
    if (result && !check)
        MessageBoxW(NULL,
            L"请保留应用和数据。错误详情位于本地应用数据目录 Scopecat\\native-start.log。",
            L"Scopecat 启动未完成", MB_OK | MB_ICONERROR);
    return (int)result;
}
