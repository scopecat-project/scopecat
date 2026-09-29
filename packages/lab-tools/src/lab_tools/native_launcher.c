#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>
#include <stdlib.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show) {
    wchar_t module[32768];
    DWORD length = GetModuleFileNameW(NULL, module, 32768);
    if (!length || length >= 32768) return 1;
    wchar_t *slash = wcsrchr(module, L'\\');
    if (!slash) return 1;
    *slash = L'\0';
    size_t capacity = 2 * wcslen(module) + wcslen(command) + 256;
    wchar_t *arguments = calloc(capacity, sizeof(wchar_t));
    if (!arguments) return 1;
    // Existing user arguments remain a Windows command line; only fixed paths
    // are added. Windows filenames cannot contain the quoting character.
    swprintf_s(arguments, capacity,
        L"\"%ls\\resources\\python\\pythonw.exe\" -I -B \"%ls\\resources\\bootstrap.py\" %ls",
        module, module, command);
    STARTUPINFOW startup = {0};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {0};
    BOOL started = CreateProcessW(NULL, arguments, NULL, NULL, FALSE,
        CREATE_NO_WINDOW, NULL, NULL, &startup, &process);
    free(arguments);
    DWORD result = 1;
    if (started) {
        WaitForSingleObject(process.hProcess, INFINITE);
        GetExitCodeProcess(process.hProcess, &result);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
    BOOL check = FALSE;
    int argc;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv) {
        for (int i = 1; i < argc; ++i)
            if (!wcscmp(argv[i], L"--check-result")) check = TRUE;
        LocalFree(argv);
    }
    if (result && !check)
        MessageBoxW(NULL,
            L"请保留应用和数据。错误详情位于本地应用数据目录 Scopecat\\native-start.log。",
            L"Scopecat 启动未完成", MB_OK | MB_ICONERROR);
    return (int)result;
}
