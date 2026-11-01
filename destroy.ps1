# Deletes EVERYTHING this project created in AWS, then verifies nothing is left.
# Usage:  .\destroy.ps1                (asks you to type the stack name to confirm)
#         .\destroy.ps1 -StackName my-stack -Region us-west-2
#
# This permanently deletes all users, chat history, uploaded-document facts,
# feedback and the knowledge base. It cannot be undone.

param(
    [string]$StackName = "loanassist-ai",
    [string]$Region = "us-east-1"
)

$ErrorActionPreference = "Stop"

function Run([string]$Title, [scriptblock]$Command) {
    Write-Host "==> $Title" -ForegroundColor Cyan
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
}

Write-Host "This will permanently delete stack '$StackName' in $Region and all of its data." -ForegroundColor Red
$answer = Read-Host "Type the stack name to confirm"
if ($answer -ne $StackName) { Write-Host "Cancelled."; exit 1 }

# Read names before the stack is gone.
$outputs = @{}
$json = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" --output json 2>$null
if ($LASTEXITCODE -eq 0 -and $json) { foreach ($o in (($json | Out-String) | ConvertFrom-Json)) { $outputs[$o.OutputKey] = $o.OutputValue } }
$fn = aws cloudformation describe-stack-resource --stack-name $StackName --region $Region --logical-resource-id LoanAssistFunction `
    --query "StackResourceDetail.PhysicalResourceId" --output text 2>$null

# 1) Buckets must be empty before CloudFormation can delete them.
foreach ($bucket in @($outputs.FrontendBucket, $outputs.DocsBucket)) {
    if ($bucket) { Run "Emptying s3://$bucket" { aws s3 rm "s3://$bucket" --recursive --region $Region --only-show-errors } }
}

# 2) Delete the stack (Lambda, API, CloudFront, Cognito, DynamoDB, KB, S3 Vectors, Guardrail, SNS, IAM).
Run "Deleting stack $StackName (CloudFront can take 10-15 minutes)" {
    sam delete --stack-name $StackName --region $Region --no-prompts
}

# 3) Things CloudFormation does not own.
if ($fn -and $fn -ne "None") {
    Run "Deleting Lambda log group" { aws logs delete-log-group --log-group-name "/aws/lambda/$fn" --region $Region 2>$null }
}
Run "Deleting LangSmith key parameter (if any)" { aws ssm delete-parameter --name /loanassist/langsmith-api-key --region $Region 2>$null }

# 4) Verify.
Write-Host ""
Write-Host "==> Verifying nothing is left" -ForegroundColor Cyan
$left = @()
aws cloudformation describe-stacks --stack-name $StackName --region $Region 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { $left += "CloudFormation stack $StackName" }
$buckets = aws s3api list-buckets --query "Buckets[?starts_with(Name, '$StackName-')].Name" --output text
if ($buckets) { $left += "S3 buckets: $buckets" }
$kbs = aws bedrock-agent list-knowledge-bases --region $Region --query "knowledgeBaseSummaries[?name=='$StackName-kb'].knowledgeBaseId" --output text
if ($kbs) { $left += "Knowledge base: $kbs" }
$tables = aws dynamodb list-tables --region $Region --query "TableNames[?starts_with(@, '$StackName-')]" --output text
if ($tables) { $left += "DynamoDB tables: $tables" }
$pools = aws cognito-idp list-user-pools --max-results 60 --region $Region --query "UserPools[?Name=='$StackName-users'].Id" --output text
if ($pools) { $left += "Cognito user pools: $pools" }
$topics = aws sns list-topics --region $Region --query "Topics[?contains(TopicArn, ':$StackName-')].TopicArn" --output text
if ($topics) { $left += "SNS topics: $topics" }
$logs = aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/$StackName-" --region $Region --query "logGroups[].logGroupName" --output text
if ($logs) { $left += "Log groups: $logs" }

if ($left.Count -eq 0) {
    Write-Host "All LoanAssist resources are deleted. Nothing from this stack will bill you." -ForegroundColor Green
    Write-Host "Shared and not deleted: the SAM artifacts bucket (stack 'aws-sam-cli-managed-default'), see README."
} else {
    Write-Host "Still present (delete these or re-run the script):" -ForegroundColor Yellow
    $left | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
    exit 2
}
