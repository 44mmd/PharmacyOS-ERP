; PharmacyOS ERP installer additions (electron-builder NSIS include).
; Pharmacy data lives in %ProgramData%\PharmacyOS (server data, backups, sales spreadsheets) and the
; per-user app config in %APPDATA%. Neither is removed by uninstall or update.
!macro customInstall
  ReadEnvStr $0 PROGRAMDATA
  CreateDirectory "$0\PharmacyOS\Backups"
  CreateDirectory "$0\PharmacyOS\Sales"
  CreateDirectory "$0\PharmacyOS\Daily Reports"
  CreateDirectory "$0\PharmacyOS\Logs"
!macroend

!macro customUnInstall
  ; Intentionally empty: pharmacy data and backups are never deleted by uninstall.
!macroend
