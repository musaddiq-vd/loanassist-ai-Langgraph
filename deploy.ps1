# One-command deployment for LoanAssist AI.
# Usage:  .\deploy.ps1            (stack "loanassist-ai" in us-east-1)
#         .\deploy.ps1 -StackName my-stack -Region us-west-2

param(
    [string]$StackName = "loanassist-ai",
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Step([string]$Title, [scriptblock]$Command) {
    Write-Host ""
    Write-Host "==> $Title" -ForegroundColor Cyan
    # CLI tools print progress on stderr; judge success by exit code, not by stderr text.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$Title failed (exit code $LASTEXITCODE)" }
}

Invoke-Step "Building backend" { sam build }
Invoke-Step "Deploying stack '$StackName' to $Region" {
    sam deploy --stack-name $StackName --region $Region
}

Write-Host ""
Write-Host "==> Reading stack outputs" -ForegroundColor Cyan
$outputsJson = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
    --query "Stacks[0].Outputs" --output json
if ($LASTEXITCODE -ne 0) { throw "Could not read stack outputs" }
$out = @{}
foreach ($o in (($outputsJson | Out-String) | ConvertFrom-Json)) { $out[$o.OutputKey] = $o.OutputValue }

Invoke-Step "Uploading knowledge-base documents" {
    aws s3 sync knowledge-base/ "s3://$($out.DocsBucket)/" --delete --region $Region
}

Write-Host ""
Write-Host "==> Syncing Knowledge Base (ingestion job)" -ForegroundColor Cyan
$jobId = aws bedrock-agent start-ingestion-job --region $Region `
    --knowledge-base-id $out.KnowledgeBaseId --data-source-id $out.DataSourceId `
    --query "ingestionJob.ingestionJobId" --output text
if ($LASTEXITCODE -ne 0) { throw "Could not start ingestion job" }
do {
    Start-Sleep -Seconds 10
    $status = aws bedrock-agent get-ingestion-job --region $Region `
        --knowledge-base-id $out.KnowledgeBaseId --data-source-id $out.DataSourceId `
        --ingestion-job-id $jobId --query "ingestionJob.status" --output text
    Write-Host "    status: $status"
} while ($status -in @("STARTING", "IN_PROGRESS"))
if ($status -ne "COMPLETE") {
    Write-Warning "Ingestion finished with status '$status'. Check the Knowledge Base in the Bedrock console."
}

Invoke-Step "Uploading frontend" {
    aws s3 sync frontend/ "s3://$($out.FrontendBucket)/" --delete --region $Region
}

# Sign-in settings come from the stack, so nothing has to be copied by hand.
$authConfig = Join-Path ([System.IO.Path]::GetTempPath()) "loanassist-auth-config.js"
$authJson = @{ domain = $out.CognitoDomain; clientId = $out.UserPoolClientId } | ConvertTo-Json -Compress
Set-Content -Path $authConfig -Encoding ascii -Value "window.LOANASSIST_AUTH = $authJson;"
Invoke-Step "Writing sign-in settings" {
    aws s3 cp $authConfig "s3://$($out.FrontendBucket)/js/auth-config.js" --region $Region `
        --content-type "application/javascript" --cache-control "no-cache"
}
Remove-Item $authConfig -ErrorAction SilentlyContinue
Invoke-Step "Clearing CloudFront cache" {
    aws cloudfront create-invalidation --distribution-id $out.DistributionId --paths "/*" --output text | Out-Null
}

Write-Host ""
Write-Host "Done! Open the app: $($out.CloudFrontUrl)" -ForegroundColor Green
Write-Host "(A brand-new CloudFront distribution can take a few minutes to start serving.)"
