<#
Helper script to create an approval request and optionally approve it.
Usage examples:
  # create approval only
  .\request_and_approve.ps1 -Title 'Install Docker' -Details 'Install Docker Desktop'

  # create and immediately approve
  .\request_and_approve.ps1 -Title 'Install Docker' -Details 'Install Docker Desktop' -Approve

This script uses Invoke-RestMethod and expects the backend at http://127.0.0.1:8000
#>

param(
    [string]$Title = "Approval request",
    [string]$Details = "Details",
    [switch]$Approve,
    [string]$ApiBase = "http://127.0.0.1:8000"
)

try {
    $payload = @{ title = $Title; details = $Details } | ConvertTo-Json
    Write-Host "Requesting approval..."
    $reqUrl = "$ApiBase/request_approval"
    $resp = Invoke-RestMethod -Uri $reqUrl -Method Post -Body $payload -ContentType 'application/json'
    $aid = $resp.approval_id
    Write-Host "Created approval_id: $aid"

    if ($Approve) {
        Write-Host "Approving now..."
        $payload2 = @{ status = 'approved' } | ConvertTo-Json
        $appUrl = "$ApiBase/approval/$aid"
        Invoke-RestMethod -Uri $appUrl -Method Post -Body $payload2 -ContentType 'application/json'
        Write-Host "Approved approval_id: $aid"
    }

    if ($PSBoundParameters.ContainsKey('Reject') -and $Reject) {
        Write-Host "Rejecting now..."
        $payload2 = @{ status = 'rejected' } | ConvertTo-Json
        $rejUrl = "$ApiBase/approval/$aid"
        Invoke-RestMethod -Uri $rejUrl -Method Post -Body $payload2 -ContentType 'application/json'
        Write-Host "Rejected approval_id: $aid"
    }
} catch {
    Write-Error "Request failed: $_"
    exit 1
}

exit 0
