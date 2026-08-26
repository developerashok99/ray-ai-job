$outputFile = "C:\Users\ashok\AppData\Local\Temp\claude\c--Users-ashok-Desktop-JobPilot-AI\d27264df-d393-48a1-86bc-758169d34fbc\tasks\bx2mqmpde.output"
$logFile = "C:\Users\ashok\Desktop\JobPilot_AI\shutdown_log.txt"

"[$(Get-Date)] Shutdown monitor started. Watching pipeline..." | Out-File $logFile -Encoding utf8

while ($true) {
    Start-Sleep -Seconds 60

    if (Test-Path $outputFile) {
        $content = Get-Content $outputFile -Raw -ErrorAction SilentlyContinue
        if ($content -match "Pipeline Complete") {
            "[$(Get-Date)] Pipeline Complete detected! Shutting down in 2 minutes..." | Out-File $logFile -Append -Encoding utf8

            # Show a popup notification
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show(
                "JobPilot AI pipeline is complete! PC will shut down in 2 minutes.`nCheck jobs_output.xlsx on your Desktop.",
                "JobPilot AI - Done",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            )

            # Schedule shutdown in 120 seconds
            shutdown /s /t 120 /c "JobPilot AI pipeline complete. Shutting down."
            "[$(Get-Date)] Shutdown command issued." | Out-File $logFile -Append -Encoding utf8
            break
        }
    }

    "[$(Get-Date)] Still running..." | Out-File $logFile -Append -Encoding utf8
}
