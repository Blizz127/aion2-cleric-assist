// Documented KWin 6 scripting API only (develop.kde.org/docs/plasma/kwin/api/):
// workspace.windowActivated, workspace.activeWindow, Window.resourceClass/desktopFileName/caption, callDBus().
function report(w) {
    callDBus("org.aion2assist.focus", "/focus", "org.aion2assist.Focus", "Report",
             w ? (w.resourceClass + "|" + w.desktopFileName + "|" + w.caption) : "");
}
workspace.windowActivated.connect(report);
report(workspace.activeWindow);
