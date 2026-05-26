# Comprehensive Form-Filling Intelligence Fix

## Problem Summary
The agent is getting stuck in validation error loops because:
1. It answers a question with an incorrect/invalid answer
2. It detects the validation error but does NOT retry with a different answer
3. It clicks "Next" again with the same invalid answer → validation error repeats
4. This creates an infinite loop (or max 12 step loop) with the agent unable to progress

**Example:** The accommodation field shows error "Please enter a valid answer" but the agent just keeps clicking Next without changing the answer.

## Root Causes

### 1. No Validation Error Recovery
- `_get_page_errors()` detects errors (line 1407-1410)
- But the code only emits a log message and continues
- Does NOT attempt to fix the field or try alternate answers
- Next loop iteration answers the SAME question with the SAME (wrong) answer

### 2. Stuck Detection is Insufficient  
- Current stuck detection (line 1365-1371) only checks if visible_labels are identical
- This fails when validation errors occur because labels stay the same but answers are invalid
- Should also track if we're getting repeated validation errors

### 3. No Fallback Strategies
- When a field validation fails, there's no fallback:
  - Try a simpler/shorter answer
  - Try a placeholder answer (e.g., "Not specified" for open text)
  - Skip the field and continue (if optional)
  - Ask user for help (manual intervention)

### 4. Insufficient Question Understanding
- Some questions need specific formats (e.g., phone numbers, dates, URLs)
- Current code doesn't validate the format before submission
- Claude answers should check if format is valid before clicking Next

## Solution Strategy

### Phase 1: Detect & Log Validation Errors Properly
```
When validation error detected:
1. Extract error message
2. Identify which field(s) caused the error
3. Log field name + error message + current answer
4. Track error count per field per page
```

### Phase 2: Implement Error Recovery Logic
```
When validation error on a field:
1. Count this error (max 3 attempts per field)
2. If attempts < 3:
   a) Try answer with different approach:
      - Longer/shorter version
      - Alternative phrasing
      - Placeholder value
   b) Re-fill the field
   c) Click Next again
3. If attempts >= 3:
   a) Try leaving field empty (if optional)
   b) Or use safe default placeholder
   c) If still fails, skip to manual
```

### Phase 3: Smart Answer Generation
```
For each question type, maintain fallback answers:

TEXT FIELD:
- Primary: AI-generated from context
- Fallback 1: Shorter version (first 100 chars)
- Fallback 2: Very short placeholder
- Fallback 3: Generic placeholder matching field type

SELECT/RADIO:
- If validation fails, try "No", "Not specified", "Prefer not to say"
- Log which option was rejected

OPEN TEXT (Accommodations, Comments):
- Primary: Full customized answer
- Fallback 1: Shorter version
- Fallback 2: Professional placeholder ("No specific accommodations needed")
- Fallback 3: Bare minimum generic answer
```

### Phase 4: Context-Aware Retry
```
Example: "Accommodations" field validation failing
- Current answer might be truncated: "nglish or German..."
- Root cause: Answer was concatenated wrong
- Recovery: Generate fresh, complete answer
- Verify answer length before filling
```

## Implementation Details

### New Function: `_retry_with_validation_error()`
```python
async def _retry_with_validation_error(
    page: Page,
    field_label: str,
    original_answer: str,
    attempt: int,
    question_context: str
) -> str:
    """
    Generate alternative answer when validation fails.
    attempt: 1-3 (higher = more aggressive fallback)
    """
    if attempt == 1:
        # Try Claude to generate better answer
        return await _ask_claude_for_answer(
            question=question_context,
            error_msg="Validation failed, try alternative phrasing",
            context=original_answer
        )
    elif attempt == 2:
        # Try shorter/simplified version
        return original_answer[:100].strip()
    else:
        # Use safe generic placeholder
        return _get_safe_placeholder(field_label)

def _get_safe_placeholder(field_label: str) -> str:
    """Return safe placeholder for field type."""
    label_lower = field_label.lower()
    if "accommodat" in label_lower or "comment" in label_lower:
        return "No specific requirements needed."
    elif "phone" in label_lower:
        return "+1234567890"
    elif "url" in label_lower or "link" in label_lower:
        return "N/A"
    elif "years" in label_lower or "experience" in label_lower:
        return "5+"
    else:
        return "Not specified"
```

### Enhanced Loop Logic
```python
# In fill_easy_apply() function:

_field_error_count = {}  # track errors per field
_max_error_attempts = 3

for step_n in range(12):
    await _answer_visible_questions(...)
    
    # Click Next
    await nxt.first.click()
    await asyncio.sleep(random.uniform(1.0, 3.5))
    
    # Check for validation errors
    _step_errs = await _get_page_errors(page)
    if _step_errs:
        for error_msg in _step_errs:
            # Identify which field had error
            field_label = _extract_error_field(page, error_msg)
            
            # Increment error count
            err_key = (step_n, field_label)
            _field_error_count[err_key] = _field_error_count.get(err_key, 0) + 1
            
            # Emit error
            _emit("apply_step", {"url": job["url"], 
                "step": f"⚠️ Validation: {error_msg} (attempt {_field_error_count[err_key]})"})
            
            # If too many attempts, break
            if _field_error_count[err_key] >= _max_error_attempts:
                log.warning("Field %s failed validation %d times - giving up", 
                    field_label, _max_error_attempts)
                break
        
        # Go back and retry current page with different answers
        if max(_field_error_count.values()) < _max_error_attempts:
            log.info("Retrying page %d with alternative answers", step_n + 1)
            # Re-answer the questions with fallback strategies
            await _answer_visible_questions_with_fallbacks(
                page, resume_text, profile, job_desc,
                attempt=_field_error_count.get((step_n, ""), 1)
            )
            # Try clicking Next again
            continue
        else:
            # Too many failures - give up
            break
```

## Key Changes Needed

### 1. File: `applier/linkedin_applier.py`

**Function: `_answer_visible_questions()`**
- Add parameter: `attempt: int = 1`
- When attempt > 1, use more conservative/fallback answers
- Add answer validation before filling

**New Function: `_answer_visible_questions_with_fallbacks()`**
- Smarter answer generation for retry attempts
- Use cached knowledge of what works/doesn't work

**Function: `fill_easy_apply()`**
- Track validation errors per field per page
- On validation error, retry current page (up to 3 times)
- Don't advance to next page until current page passes validation
- Max total attempts per field: 3
- After max attempts: try empty field or generic placeholder

### 2. File: `applier/external_applier.py`

**Apply same error recovery logic**
- Same `_answer_visible_questions_with_fallbacks()` function
- Same max attempt tracking
- Same fallback strategies

### 3. File: `core/config.py`

**Add configuration option:**
```yaml
# Form filling strategy
form_validation_max_attempts: 3  # max retry attempts per field
form_validation_use_fallbacks: true  # try fallback answers on error
form_skip_optional_on_error: true  # skip optional fields if validation fails
```

## Validation Error Detection Enhancement

### Better Error Field Extraction
```python
async def _extract_error_field(page: Page, error_msg: str) -> str:
    """
    Find which field label corresponds to the error message.
    """
    # Look for field with error aria-invalid or error class
    for label in await page.query_selector_all("label"):
        label_text = await label.text_content()
        # Check if this label's input has error styling
        try:
            # Get the associated input
            field_id = await label.get_attribute("for")
            if field_id:
                field = await page.query_selector(f"#{field_id}")
                has_error = await field.evaluate("el => el.classList.contains('has-error') || el.hasAttribute('aria-invalid')")
                if has_error:
                    return label_text.strip()
        except:
            pass
    
    # Fallback: try to match error message to a visible field
    return "Unknown field"
```

### Track Validation Error Patterns
```python
# Cache what answers cause validation errors
_validation_failure_cache = {}

def _should_skip_answer(question: str, answer: str) -> bool:
    """Check if this question+answer combination previously failed validation."""
    key = (question.lower()[:50], answer.lower()[:50])
    return _validation_failure_cache.get(key, False)

def _mark_answer_failed(question: str, answer: str):
    """Mark this answer as causing validation error."""
    key = (question.lower()[:50], answer.lower()[:50])
    _validation_failure_cache[key] = True
```

## Testing Scenarios

1. **Accommodation field validation failure:**
   - Detect: "Please enter a valid answer"
   - Retry 1: Generate better accommodation answer
   - Retry 2: Use shorter version
   - Retry 3: Use generic placeholder

2. **Required field with no answer:**
   - Detect: "This field is required"
   - Retry: Generate sensible default

3. **Format validation (phone, email, URL):**
   - Detect: "Invalid format"
   - Retry: Adjust format based on field type

4. **Stuck on same page (multiple validation errors):**
   - After 3 fields fail on same page: break and go manual
   - Log which fields consistently fail

## Success Metrics

- [ ] Agent no longer gets stuck in infinite validation loops
- [ ] Validation errors trigger intelligent retries
- [ ] Agent tries up to 3 different answers per field
- [ ] Agent successfully completes forms with difficult validation rules
- [ ] Forms that can't be completed are sent to manual after max attempts
- [ ] All validation attempts are logged with field name + error + answer tried

## Priority Order

1. **Critical:** Prevent infinite validation loops (add max attempt counter + break)
2. **High:** Implement basic fallback answers for common field types  
3. **High:** Log which field caused validation error
4. **Medium:** Claude-powered alternative answer generation on retry
5. **Medium:** Track validation failure patterns across applications
6. **Low:** Add configuration options for validation strategy
