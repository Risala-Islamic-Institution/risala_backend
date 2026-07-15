$ErrorActionPreference = "Stop"
$commitCount = 0

function Commit($files, $message) {
    foreach ($f in $files) { git add $f }
    $status = git status --porcelain
    if ($status) {
        git commit -m $message
        $script:commitCount++
    }
}

# Collect all changed or untracked non-sensitive files
$changed = git diff --name-only
$untracked = git ls-files --others --exclude-standard

$allFiles = @($changed) + @($untracked) | Where-Object {
    $_ -and
    $_ -notmatch "\.env" -and
    $_ -notmatch "\.django$" -and
    $_ -notmatch "\.local$" -and
    $_ -notmatch "secrets"
}

# Group by feature area for meaningful commits
$groups = @{
    "config/settings"             = @()
    "risala_backend/courses"      = @()
    "risala_backend/payments"     = @()
    "risala_backend/users"        = @()
    "risala_backend/bookings"     = @()
    "risala_backend/scheduling"   = @()
    ".agents"                     = @()
    ".claude"                     = @()
    ".windsurf"                   = @()
    "other"                       = @()
}

foreach ($f in $allFiles) {
    if ($f -match "^config/settings") { $groups["config/settings"] += $f }
    elseif ($f -match "risala_backend/courses") { $groups["risala_backend/courses"] += $f }
    elseif ($f -match "risala_backend/payments") { $groups["risala_backend/payments"] += $f }
    elseif ($f -match "risala_backend/users") { $groups["risala_backend/users"] += $f }
    elseif ($f -match "risala_backend/bookings") { $groups["risala_backend/bookings"] += $f }
    elseif ($f -match "risala_backend/scheduling") { $groups["risala_backend/scheduling"] += $f }
    elseif ($f -match "^\.agents") { $groups[".agents"] += $f }
    elseif ($f -match "^\.claude") { $groups[".claude"] += $f }
    elseif ($f -match "^\.windsurf") { $groups[".windsurf"] += $f }
    else { $groups["other"] += $f }
}

$messages = @{
    "config/settings"           = "chore(settings): update local/base settings"
    "risala_backend/courses"    = "feat(courses): update course models, views and serializers"
    "risala_backend/payments"   = "feat(payments): update Stripe checkout and payment views"
    "risala_backend/users"      = "feat(users): update user profile and authentication logic"
    "risala_backend/bookings"   = "feat(bookings): update booking views and serializers"
    "risala_backend/scheduling" = "feat(scheduling): update scheduling and availability logic"
    ".agents"                   = "chore: sync AI agent context files"
    ".claude"                   = "chore: sync Claude AI context files"
    ".windsurf"                 = "chore: sync Windsurf AI context files"
    "other"                     = "chore: miscellaneous project file updates"
}

foreach ($key in $groups.Keys) {
    if ($groups[$key].Count -gt 0) {
        # Commit each file in the group as its own separate commit
        foreach ($f in $groups[$key]) {
            Commit $f $messages[$key]
        }
    }
}

if ($commitCount -eq 0) {
    Write-Host "⚠️  No changes to commit in backend."
}

# Add 32 extra commits to ensure GitHub contribution graph is populated
Write-Host "Adding extra contribution commits..."
for ($i=1; $i -le 32; $i++) {
    $date = Get-Date
    "Contribution update $i on $date" > contribution_stats.txt
    git add contribution_stats.txt
    git commit -m "chore(stats): update contribution stats $i"
    $script:commitCount++
}

Write-Host "✅ Created $commitCount commits!"
git push
Write-Host "🚀 Backend commits pushed successfully!"
