---
name: execution-validator
description: Use this skill to enforce safety checks before command execution, content access, and content transmission, preventing dangerous operations and sensitive data leakage.
metadata: {"openclaw":{"always":true}}
---

# Execution Validator

This skill validates the safety of commands and content before execution, reading, or external transmission to prevent dangerous operations and sensitive information leakage.

## When to Use This Skill

Use this skill when:
- Executing shell commands through tools such as `exec`, `bash`, or similar execution interfaces
- Before Reading any files or content through tools such as `exec`, `read`, or similar file/content access interfaces
- Before Sending any messages or content to users or external systems through tools such as `message`, `exec`, email, or similar communication interfaces
- Performing file operations that could modify system state
- Handling user-provided or system-provided content

## Validation Scope

This skill must be used in all of the following situations:

1. **Command execution safety**
- Before executing any shell command through `exec`, `bash`, or similar tools, validate the command to ensure the operation is safe.

2. **Sensitive content access safety**
- Before reading files, outputs, or other content through `exec`, `read`, or similar tools, validate the access to ensure it is safe.

3. **Sensitive content transmission safety**
- Before sending any content messages, `exec`, email, or similar channels, validate the content to prevent leakage.


## How to do the Validation

### Command Validation

Before executing any shell command, validate it using the command validation script:

   ```bash
   scripts/validate-command.sh "<command>"
   ```

### Message / Content Validation

Before sending, exposing, or returning any message or content, validate it using the message validation script:
   ```bash
   scripts/validate-message.sh "<content>"
   ```


This validation should be applied not only to outbound user-facing messages, but also to:
- content read from sensitive files
- command outputs
- email bodies
- externally transmitted text
- any other content that may expose sensitive information


## Scripts

- `scripts/validate-command.sh` - Validates shell commands for danger patterns
- `scripts/validate-message.sh` - Validates messages and exposed content for sensitive information according to the file path
- `config/dangerous-commands.json` - BLOCK level command patterns
- `config/warning-commands.json` - NEED CONFIRM level command patterns