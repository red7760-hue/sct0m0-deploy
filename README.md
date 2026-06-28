This is written to test out nidec card dispenser. Run the below via powershell in admin mode. This script should try to do the below.

- Download and install Python & pySerial
- Initialize the card dispenser.
- If the card dispenser is already initialized and there is a jam or any other issues, the script will display an error

irm https://raw.githubusercontent.com//red7760-hue/sct0m0-deploy/main/Bootstrap-SCT0M0.ps1 -OutFile Bootstrap-SCT0M0.ps1
.\Bootstrap-SCT0M0.ps1 -TestRun
