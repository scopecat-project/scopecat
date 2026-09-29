#import <Cocoa/Cocoa.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>
#include <errno.h>

extern char **environ;

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
        pid_t child;
        int status = 0;
        int error = posix_spawn(&child, python, NULL, NULL, arguments, environ);
        free(arguments);
        if (!error) {
            while (waitpid(child, &status, 0) < 0) {
                if (errno != EINTR) { error = errno; break; }
            }
        }
        int result = error ? 1 : WIFEXITED(status) ? WEXITSTATUS(status) : 1;
        if (result && !check) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Scopecat 启动未完成";
            alert.informativeText = @"请保留应用和数据。错误详情位于 ~/Library/Application Support/Scopecat/native-start.log。";
            [alert runModal];
        }
        return result;
    }
}
