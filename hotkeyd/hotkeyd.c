// Global hotkeys through Carbon RegisterEventHotKey: needs no Accessibility or Input Monitoring permission.
// usage: julius-hotkeyd name=ctrl+alt+s name2=ctrl+alt+j  -> prints "failed name why" lines, "ready name ...", then "fire name" per press.
#include <Carbon/Carbon.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <pthread.h>
#include <stdlib.h>
#include <unistd.h>

static char names[64][64];
static const struct { const char *k; int code; } KEYS[] = {
 {"a",0},{"s",1},{"d",2},{"f",3},{"h",4},{"g",5},{"z",6},{"x",7},{"c",8},{"v",9},{"b",11},{"q",12},{"w",13},{"e",14},{"r",15},{"y",16},{"t",17},
 {"1",18},{"2",19},{"3",20},{"4",21},{"6",22},{"5",23},{"9",25},{"7",26},{"8",28},{"0",29},{"o",31},{"u",32},{"i",34},{"p",35},{"l",37},{"j",38},{"k",40},{"n",45},{"m",46},{"space",49},{"escape",53},{NULL,0}};

static OSStatus on_key(EventHandlerCallRef c, EventRef e, void *u) {
    EventHotKeyID id; GetEventParameter(e, kEventParamDirectObject, typeEventHotKeyID, NULL, sizeof id, NULL, &id);
    if (id.id < 64 && names[id.id][0]) { printf("fire %s\n", names[id.id]); fflush(stdout); }
    return noErr;
}

// exit when the parent closes our stdin (parent died or stopped us)
static void *watch_parent(void *u) { char b[64]; while (read(0, b, sizeof b) > 0) {} _exit(0); return NULL; }

int main(int argc, char **argv) {
    if (!getenv("JULIUS_HOTKEYD_NOWATCH")) { pthread_t t; pthread_create(&t, NULL, watch_parent, NULL); }
    setvbuf(stdout, NULL, _IOLBF, 0);
    EventTypeSpec spec = { kEventClassKeyboard, kEventHotKeyPressed };
    InstallEventHandler(GetApplicationEventTarget(), on_key, 1, &spec, NULL, NULL);
    char ready[1024] = "ready";
    for (int i = 1; i < argc && i < 64; i++) {
        char *eq = strchr(argv[i], '='); if (!eq) { printf("failed %s bad-argument\n", argv[i]); continue; }
        char name[64]; snprintf(name, sizeof name, "%.*s", (int)(eq - argv[i]), argv[i]);
        UInt32 mods = 0; int code = -1; char buf[128]; snprintf(buf, sizeof buf, "%s", eq + 1);
        for (char *p = buf; *p; p++) *p = tolower((unsigned char)*p);
        for (char *tok = strtok(buf, "+"); tok; tok = strtok(NULL, "+")) {
            while (*tok == '<' || *tok == ' ') tok++;
            char *end = tok + strlen(tok); while (end > tok && (end[-1] == '>' || end[-1] == ' ')) *--end = 0;
            if (!strcmp(tok, "ctrl") || !strcmp(tok, "control")) mods |= controlKey;
            else if (!strcmp(tok, "alt") || !strcmp(tok, "option") || !strcmp(tok, "opt")) mods |= optionKey;
            else if (!strcmp(tok, "cmd") || !strcmp(tok, "command")) mods |= cmdKey;
            else if (!strcmp(tok, "shift")) mods |= shiftKey;
            else for (int k = 0; KEYS[k].k; k++) if (!strcmp(tok, KEYS[k].k)) code = KEYS[k].code;
        }
        if (code < 0) { printf("failed %s unknown-key\n", name); continue; }
        EventHotKeyRef ref; EventHotKeyID id = { 'JLUS', (UInt32)i };
        OSStatus st = RegisterEventHotKey(code, mods, id, GetApplicationEventTarget(), 0, &ref);
        if (st == noErr) { snprintf(names[i], 64, "%s", name); strncat(ready, " ", sizeof ready - strlen(ready) - 1); strncat(ready, name, sizeof ready - strlen(ready) - 1); }
        else printf("failed %s status=%d\n", name, (int)st);
    }
    puts(ready); fflush(stdout);
    CFRunLoopRun();
    return 0;
}
