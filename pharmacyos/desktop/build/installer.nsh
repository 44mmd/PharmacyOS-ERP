; PharmacyOS ERP installer additions (electron-builder NSIS include).
; Pharmacy data lives in %ProgramData%\PharmacyOS (server environment, backups, sales spreadsheets, logs)
; and the per-user app config in %APPDATA%. Updating never touches them.
!macro customInstall
  ReadEnvStr $0 PROGRAMDATA
  CreateDirectory "$0\PharmacyOS\Backups"
  CreateDirectory "$0\PharmacyOS\Sales"
  CreateDirectory "$0\PharmacyOS\Daily Reports"
  CreateDirectory "$0\PharmacyOS\Logs"
!macroend

; Uninstall removes the app only. The pharmacy server (its database) is removed only when the person
; uninstalling explicitly says so; never during an update and never in a silent uninstall (default No).
; Backups are always kept.
!macro customUnInstall
  ${ifNot} ${isUpdated}
    MessageBox MB_YESNO|MB_ICONEXCLAMATION|MB_DEFBUTTON2 "Also remove the PharmacyOS pharmacy server and its database from this computer?$\r$\n$\r$\nChoose No to keep the pharmacy's data (recommended). A final backup is taken first; all backups stay in C:\ProgramData\PharmacyOS\Backups.$\r$\n$\r$\nهل تريد حذف خادم الصيدلية وقاعدة بياناته من هذا الجهاز أيضًا؟ اختر «لا» للاحتفاظ ببيانات الصيدلية (موصى به)." /SD IDNO IDNO keepServer
      nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\server\deploy\windows\uninstall-server.ps1"'
    keepServer:
  ${endIf}
!macroend
