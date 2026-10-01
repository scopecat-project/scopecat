#import <Cocoa/Cocoa.h>
#include <mach-o/dyld.h>
// Stable CPython entry point; keep the native executable as the app's process.
extern int Py_BytesMain(int argc, char **argv);

int main(int argc, char **argv) {
    @autoreleasepool {
        uint32_t size = 0;
        _NSGetExecutablePath(NULL, &size);
        char *path = malloc(size);
        if (!path || _NSGetExecutablePath(path, &size)) return 1;
        NSString *executable = [[NSString stringWithUTF8String:path] stringByResolvingSymlinksInPath];
        free(path);
        NSString *resources = [[[executable stringByDeletingLastPathComponent]
            stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"Resources"];
        const char *python = [[resources stringByAppendingPathComponent:@"python/bin/python3"] fileSystemRepresentation];
        const char *script = [[resources stringByAppendingPathComponent:@"bootstrap.py"] fileSystemRepresentation];
        char **arguments = calloc((size_t)argc + 5, sizeof(char *));
        if (!arguments) return 1;
        arguments[0] = (char *)python;
        arguments[1] = "-I";
        arguments[2] = "-B";
        arguments[3] = (char *)script;
        BOOL check = NO;
        for (int i = 1; i < argc; i++) {
            arguments[i + 3] = argv[i];
            if (!strcmp(argv[i], "--check-result")) check = YES;
        }
        // execve(python, ...) loses NSBundle.mainBundle's app identity, which
        // prevents macOS from placing the status item in the menu bar. Embed
        // Python on this main thread; argv[0] still selects the bundled runtime
        // for sys.executable and backend subprocesses.
        int result = Py_BytesMain(argc + 3, arguments);
        free(arguments);
        if (result && !check) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Scopecat 启动未完成";
            alert.informativeText = @"请保留应用和数据。错误详情位于 ~/Library/Application Support/Scopecat/native-start.log。";
            [alert runModal];
        }
        return result;
    }
}
