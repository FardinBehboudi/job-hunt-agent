# Smart Applier Branch — Setup Instructions

## Step 1: Create the branch
```bash
git checkout -b feature/smart-applier
```

## Step 2: Run the 3 implementation scripts IN ORDER

```bash
# 1. Wizard fixes + smart_filler.py + external applier upgrades
python fix_applier_wizard.py

# 2. Memory/learning system
python implement_memory.py

# 3. Universal field handler (all input types)
python fix_field_handlers.py
```

## Step 3: Verify syntax
```bash
python -m py_compile applier/linkedin_applier.py && echo "OK"
python -m py_compile applier/external_applier.py && echo "OK"
python -m py_compile applier/smart_filler.py    && echo "OK"
python -m py_compile applier/memory.py          && echo "OK"
python -m py_compile applier/applier.py         && echo "OK"
```

## Step 4: Test with one job
- Run dashboard, pick 1 job, Apply Selected
- Watch the Apply Log for: 🧠 Smart fill, ✎ filled fields, memory hints

## Step 5: Commit
```bash
git add -A
git commit -m "feat: smart applier with vision, memory and universal field handling

New files:
  - applier/smart_filler.py  — Claude Sonnet vision form filler
  - applier/memory.py        — SQLite learning/memory system

LinkedIn Easy Apply fixes:
  - Refresh _ws scope on every step (fixes stuck step 2)
  - Blur/change/input events before Next (triggers validation early)
  - Page-level button fallback + Claude vision if still not found
  - Select dropdown support in retry logic
  - Claude vision smart_fill_form() when stuck ≥ 1 step
  - Universal field handler: text/number/select/radio/checkbox/
    date/typeahead/artdeco-combobox/file/React-controlled inputs
  - Memory: check before answering, save after success

External apply upgrades:
  - _ai_decide_form_actions: Haiku → Sonnet
  - browser-use fallback → Claude vision smart_apply_page()
  - New-tab detection polls 4s
  - Unknown platforms try smart_apply before manual
  - Memory context in all AI prompts

Memory system:
  - Remembers Q&A pairs across applications
  - Platform-specific lessons from failures (Claude-extracted)
  - Field→value mappings that succeeded
  - Injected into every Claude prompt"
```
