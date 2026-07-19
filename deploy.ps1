# deploy.ps1 — 部署 battery-dv-report-review skill 到用户级技能目录
# 目标位置 ~/.agents/skills/ 同时被 GitHub Copilot CLI、OpenCode（及 kimi CLI）发现。
# 用法：powershell -ExecutionPolicy Bypass -File deploy.ps1
$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$dst = Join-Path $env:USERPROFILE '.agents\skills\battery-dv-report-review'

robocopy $src $dst /MIR /NFL /NDL /NJH `
    /XD .venv __pycache__ .git `
    /XF *.extracted.md *.checks.md *.review.md batch-review-summary.md *.docx *.pptx _px.png | Out-Host
if ($LASTEXITCODE -gt 7) { throw "robocopy 失败，退出码 $LASTEXITCODE" }
Write-Host "OK: 已部署到 $dst"
Write-Host "验证：在 copilot 或 opencode 中问『你有哪些可用的 skill』，应出现 battery-dv-report-review"
