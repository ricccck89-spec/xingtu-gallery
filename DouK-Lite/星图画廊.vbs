Option Explicit
' 星图画廊 无窗口启动器。
' 双击它（或双击它生成的「星图画廊」快捷方式）＝ 打开画廊，不弹黑框。
' 第一次运行会在 DouK-Lite 目录和桌面各放一枚带星图的快捷方式，只做一次。
Dim fso, sh, root, target, lnk, rc, logFile, note, ts, line, sc, desk
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)

MakeShortcuts

target = root & "\scripts\启动.cmd"
If Not fso.FileExists(target) Then
    MsgBox "找不到启动脚本：" & vbCrLf & target & vbCrLf & vbCrLf & _
           "请确认 DouK-Lite 文件夹是完整的（scripts 不能删）。", 48, "星图画廊"
    WScript.Quit 1
End If

rc = sh.Run(Chr(34) & target & Chr(34), 0, True)

If rc <> 0 Then
    note = ""
    logFile = root & "\logs\启动日志.txt"
    If fso.FileExists(logFile) Then
        Set ts = fso.OpenTextFile(logFile, 1, False)
        Do Until ts.AtEndOfStream
            line = ts.ReadLine
            If InStr(line, "ERROR") > 0 Then note = note & line & vbCrLf
        Loop
        ts.Close
    End If
    If note = "" Then note = "没发现明确报错。可以打开 logs\server.log 看看。"
    MsgBox "星图画廊没能启动。" & vbCrLf & vbCrLf & note, 16, "星图画廊"
End If

' ---- 只在第一次运行时补快捷方式（目录里 + 桌面），之后不再碰 ----
' 想让它再补一次：删掉 cache\快捷方式已完成.txt 再双击本文件。
Sub MakeShortcuts()
    Dim stamp
    stamp = root & "\cache\快捷方式已完成.txt"
    If fso.FileExists(stamp) Then Exit Sub
    On Error Resume Next
    lnk = root & "\星图画廊.lnk"
    Set sc = sh.CreateShortcut(lnk)
    sc.TargetPath = sh.ExpandEnvironmentStrings("%SystemRoot%\System32\wscript.exe")
    sc.Arguments = Chr(34) & WScript.ScriptFullName & Chr(34)
    sc.WorkingDirectory = root
    sc.IconLocation = root & "\web\icon.ico,0"
    sc.Description = "星图画廊 · 博主作品库"
    sc.Save
    If Err.Number = 0 Then
        desk = sh.SpecialFolders("Desktop") & "\星图画廊.lnk"
        If Not fso.FileExists(desk) Then fso.CopyFile lnk, desk, True
        Err.Clear
        If fso.FolderExists(root & "\cache") Then
            fso.CreateTextFile(stamp, True).WriteLine "2026-11-16"
        End If
    End If
    Err.Clear
    On Error Goto 0
End Sub

