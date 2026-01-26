"""
Update existing GitHub issues with new hunter-specific fields.
"""
import os
import json
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

GITHUB_API_URL = "https://api.github.com/repos/pirateantalis-cyber/HunterSimOptimizer/issues"

# Your IRL values from the game
UPDATES = {
    1: {  # Borge Issue #1
        "hunter": "Borge",
        "new_fields": """
### Damage Dealt - Best Run

12.89M

### Damage Dealt - Avg

10.33M

### [Borge] Crit Hits - Run Avg

1.69k

### [Borge] Extra Damage from Crits - Avg

3.89M

### [Borge] Hellfire Torch Damage - Avg

763.34M
"""
    },
    2: {  # Ozzy Issue #2
        "hunter": "Ozzy",
        "new_fields": """
### Damage Dealt - Best Run

5.76M

### Damage Dealt - Avg

4.43M

### [Ozzy] HP Removed by Soul of Snek - Avg

127.67M

### [Ozzy] Multistrike Hits - Run Avg

_No response_

### [Ozzy] Extra Damage from Multistrike - Avg

_No response_
"""
    },
    3: {  # Knox Issue #3
        "hunter": "Knox",
        "new_fields": """
### Damage Dealt - Best Run

598.68k

### Damage Dealt - Avg

568.48k

### [Knox] Extra Salvos - Run Avg

17.11k

### [Knox] Extra Damage from Salvos - Avg

68.69k

### [Knox] Torpedo Damage - Avg

1.67B
"""
    }
}


def get_github_token():
    """Get GitHub token from environment."""
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        # Try to get from gh CLI
        import subprocess
        try:
            result = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True)
            if result.returncode == 0:
                token = result.stdout.strip()
        except FileNotFoundError:
            pass
    return token


def fetch_issue(issue_number: int, token: str) -> dict:
    """Fetch a single issue from GitHub."""
    url = f"{GITHUB_API_URL}/{issue_number}"
    req = Request(url)
    req.add_header('Authorization', f'token {token}')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    
    with urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))


def update_issue(issue_number: int, body: str, token: str):
    """Update an issue body on GitHub."""
    url = f"{GITHUB_API_URL}/{issue_number}"
    
    data = json.dumps({"body": body}).encode('utf-8')
    
    req = Request(url, data=data, method='PATCH')
    req.add_header('Authorization', f'token {token}')
    req.add_header('Accept', 'application/vnd.github.v3+json')
    req.add_header('Content-Type', 'application/json')
    
    with urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))


def insert_new_fields(body: str, new_fields: str) -> str:
    """Insert new fields after XP Gained - Avg section."""
    # Find the location after "### XP Gained - Avg" and its value
    marker = "### Build JSON (from Save/Export)"
    
    if marker in body:
        # Insert the new fields before "### Build JSON"
        parts = body.split(marker)
        return parts[0].rstrip() + "\n" + new_fields.strip() + "\n\n" + marker + parts[1]
    else:
        # Fallback: append at the end before Additional Notes
        marker2 = "### Additional Notes"
        if marker2 in body:
            parts = body.split(marker2)
            return parts[0].rstrip() + "\n" + new_fields.strip() + "\n\n" + marker2 + parts[1]
    
    return body + "\n" + new_fields


def main():
    token = get_github_token()
    if not token:
        print("ERROR: No GitHub token found!")
        print("Set GITHUB_TOKEN environment variable or login with 'gh auth login'")
        return
    
    print(f"Found GitHub token: {token[:8]}...")
    
    for issue_num, update_info in UPDATES.items():
        print(f"\n{'='*60}")
        print(f"Updating Issue #{issue_num} ({update_info['hunter']})")
        print('='*60)
        
        try:
            # Fetch current issue
            issue = fetch_issue(issue_num, token)
            current_body = issue.get('body', '')
            
            # Check if already updated (has new fields)
            if "### Damage Dealt - Best Run" in current_body:
                print(f"  Issue #{issue_num} already has new fields, skipping...")
                continue
            
            # Insert new fields
            new_body = insert_new_fields(current_body, update_info['new_fields'])
            
            # Preview
            print(f"  Current body length: {len(current_body)}")
            print(f"  New body length: {len(new_body)}")
            
            # Update the issue
            result = update_issue(issue_num, new_body, token)
            print(f"  SUCCESS! Issue #{issue_num} updated at {result.get('updated_at', 'unknown')}")
            
        except HTTPError as e:
            print(f"  ERROR updating issue #{issue_num}: {e}")
            if hasattr(e, 'read'):
                print(f"  Response: {e.read().decode('utf-8')}")
        except Exception as e:
            print(f"  ERROR: {e}")
    
    print("\n" + "="*60)
    print("Done! All issues updated.")
    print("="*60)


if __name__ == "__main__":
    main()
