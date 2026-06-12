@echo off
set "SRC=C:\Users\rober\Desktop\merged website\storage"
set "DST=C:\VCA_Backup"
if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%" /MIR /R:1 /W:3 /COPY:DAT /DCOPY:DAT /XJ /FFT /Z /NP /LOG+:"%DST%\backup.log"
exit /B %ERRORLEVEL%
