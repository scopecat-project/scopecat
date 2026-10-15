#import <Cocoa/Cocoa.h>
#include <mach-o/dyld.h>
// Stable CPython entry point; keep the native executable as the app's process.
extern int Py_BytesMain(int argc, char **argv);

int main(int argc, char **argv) {
    char *python;
    char *script;
    @autoreleasepool {
        uint32_t size = 0;
        _NSGetExecutablePath(NULL, &size);
        char *path = malloc(size);
        if (!path || _NSGetExecutablePath(path, &size)) return 1;
        NSString *executable = [[NSString stringWithUTF8String:path] stringByResolvingSymlinksInPath];
        free(path);
        NSString *resources = [[[executable stringByDeletingLastPathComponent]
            stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"Resources"];
        python = strdup([[resources stringByAppendingPathComponent:@"python/bin/python3"] fileSystemRepresentation]);
        script = strdup([[resources stringByAppendingPathComponent:@"bootstrap.py"] fileSystemRepresentation]);
    }
    char **arguments = calloc((size_t)argc + 4, sizeof(char *));
    if (!arguments || !python || !script) {
        free(arguments);
        free(python);
        free(script);
        return 1;
    }
    arguments[0] = python;
    arguments[1] = "-I";
    arguments[2] = "-B";
    arguments[3] = script;
    BOOL check = NO;
    for (int i = 1; i < argc; i++) {
        arguments[i + 3] = argv[i];
        if (!strcmp(argv[i], "--check-result")) check = YES;
    }
    // Keep the native process's bundle identity, while argv[0] selects the
    // bundled interpreter for sys.executable and backend subprocesses.
    // No native autorelease pool may span Py_BytesMain: it finalizes Python
    // before returning, so draining that pool could call PyObjC after shutdown.
    // PyObjC manages its own pools during the interpreter's lifetime.
    int result = Py_BytesMain(argc + 3, arguments);
    free(arguments);
    free(python);
    free(script);
    if (result && !check) {
        @autoreleasepool {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Scopecat 启动未完成";
            alert.informativeText = @"请保留应用和数据。错误详情位于 ~/Library/Application Support/Scopecat/native-start.log。";
            [alert runModal];
        }
    }
    return result;
}
