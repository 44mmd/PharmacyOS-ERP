; PharmacyOS ERP installer additions (electron-builder NSIS include).
; Pharmacy data lives in %ProgramData%\PharmacyOS (server data, backups, sales spreadsheets) and the
; per-user app config in %APPDATA%. Neither is removed by uninstall or update.
!macro customInstall
  CreateDirectory "$COMMONPROGRAMDATA\PharmacyOS\Backups"
  CreateDirectory "$COMMONPROGRAMDATA\PharmacyOS\Sales"
  CreateDirectory "$COMMONPROGRAMDATA\PharmacyOS\Daily Reports"
  CreateDirectory "$COMMONPROGRAMDATA\PharmacyOS\Logs"
!macroend

!macro customUnInstall
  ; Intentionally empty: pharmacy data and backups are never deleted by uninstall.
!macroend
