---
name: solidworks-project
description: Run a whole multi-part SolidWorks product project from a project folder, with an AGENT_CONTEXT.md as the single source of truth and tracklist. Use when the user wants to model a complete product, machine or assembly (not one part), starts or continues "the project", points at a project folder, or says things like "model my product", "build the whole assembly", "continue the CAD project", "modelar o projeto", "continuar o projeto". On first use it interviews the user in depth, collects every relevant file into the project folder, inventories the existing CAD, and writes AGENT_CONTEXT.md; afterwards it works module by module against it and keeps it up to date.
---

# SolidWorks product project (AGENT_CONTEXT.md workflow)

> **First SolidWorks request of the session:** call `plugin_updates {"action": "status"}` once. If its state is `ask_user`, ask the user in one short question whether to turn on automatic updates for the SolidCADWorks plugin, then call it with `"enable"` or `"decline"` and carry on. For any other state, say nothing about updates.

A product is modelled over many sessions, often by several agents. Everything an agent needs to continue must
live in files in the project folder, not in a conversation. This skill sets that up and then drives the work.

Files this workflow keeps in the project folder:

| File | Purpose |
|---|---|
| `AGENT_CONTEXT.md` | The spec, the single source of truth and the tracklist. Read fully before modelling anything. |
| `DECISIONS_LOG.md` | Append-only log of design decisions (`CAD-001`, `CAD-002`, …), with the reason for each. |
| `MCP_ISSUES.md` | Optional local log of tool problems; every problem is also sent with `report_issue`. |

## Step 1: find the project and its context

1. Ask for the project folder if the user has not named one. Never guess a folder.
2. Look for `AGENT_CONTEXT.md` there, and also `*Agent_Context*.md`, `*agent context*.md` or `CLAUDE.md`, including
   in a `99_Reference` subfolder.
3. **Found:** read it all. Read `DECISIONS_LOG.md` too. Then go to Step 6. Do not interview again; ask only about gaps
   that block the next module.
4. **Not found:** do Steps 2–5 before modelling anything.

## Step 2: interview the user (grill them)

The goal is to know the product well enough that you could model it without asking again. Ask in rounds of a few
focused questions. Use the question tool when available, with options plus "other". Push back on vague answers
("about 600 wide" → "is 600 a hard limit or a target?"). Keep going until each topic below is covered or the user
says it is unknown. Record unknowns as open questions; don't invent answers.

1. **Identity and purpose:** what the product is, who uses it, what problem it solves, the stage (concept,
   prototype, production), and the deadline or what the model is for (renders, prototype, manufacture, FEA).
2. **Function:** what it physically does, step by step; moving parts and their ranges; states (open/closed,
   loaded/empty); how it is assembled, serviced and cleaned.
3. **Envelope and layout:** overall dimensions with tolerances; the vertical/horizontal stack of zones or
   modules; what is fixed by the user and what is free.
4. **Hard requirements vs preferences.** Tag every value:
   - `[USER]`: decided by the user; never change it without asking.
   - `[DESIGN]`: proposed by you or the spec; may change within about ±10 %, logged.
   - `[VERIFY]`: model as written, confirm later (supplier data, test).
   - Also clearances that must hold, e.g. "≥ 2.5 mm each side for the falling part".
5. **Look:** reference images or renders. Which one wins in a conflict? Colours, finishes, brand look.
6. **Materials and processes:** per part where known (sheet metal, injection moulding, CNC, 3D print, foam), and
   the manufacturing rules to respect.
7. **Bought parts:** motors, sensors, bearings, slides, electronics, hardware, each with exact maker part number.
   Which have CAD (STEP) and which need a placeholder envelope?
8. **Standards:** fasteners and norms (ISO/DIN), units, drawing standard. Fasteners are inserted from vendor CAD
   (e.g. TraceParts) by the user. You model the holes to the norm and list what is needed; you don't model
   fasteners.
9. **Conventions:** global coordinate system (origin, axes directions); part-number scheme; file names; folder
   structure; custom properties wanted (part number, description, material, supplier…).
10. **Existing work:** what is already modelled, what is reused, what must not be touched.
11. **Authority:** which decisions you may take alone and which must come back to the user. Default: you decide
    `[DESIGN]` gaps and log them; you stop for anything `[USER]`, the main dimension stack, or a hard clearance.
12. **Checks:** clearances, interferences, motions and service paths that must be proven in CAD before a module
    counts as done.

## Step 3: ask for the files

Ask the user to put everything relevant in the project folder, then wait until they say it is done. Suggested
layout (adapt to their scheme):

```
<project>/
  99_Reference/        spec documents, PRD, sketches, renders/, photos, datasheets
  90_Purchased_Parts/  <Vendor>/ STEP files of bought parts, named BUY_<Vendor>_<PartNo>
  91_Fasteners/        vendor fastener CAD (user-supplied), FASTENER_LIST_<module>.md (written by you)
  00_Final_Assembly/   skeleton / layout part, top assembly
  01_<Module>/ 02_<Module>/ …   one folder per module or subassembly
```

Include: already-modelled parts, assemblies and drawings (`.SLDPRT`, `.SLDASM`, `.SLDDRW`); STEP/IGES of bought
parts; spec and requirement documents; images; spreadsheets of dimensions or BOMs.

## Step 4: inventory everything

Read and inspect every file. Don't summarise from names.

- **Documents and images:** read them; note every number with its source.
- **Each part:** open it (`open_document`), then run `inspect_model` (exact box, volume, bodies), the material and
  custom properties, and `capture_viewport`. Look at the capture. Note the origin and coordinate conventions it
  uses. Close it when done.
- **Each assembly:** run `list_components`, `inspect_assembly_kinematics` (mates, fixed/floating) and
  `check_interference`, and take a capture.
- **Each bought STEP:** import it once to a native copy (`add_component` / `open_document` does this). Measure its
  box and its mounting features.
- Call `restart_solidworks` after every few large files and whenever a result carries a MEMORY WARNING.

Write down conflicts between sources and ask the user to settle the ones that matter.

## Step 5: write AGENT_CONTEXT.md

Use this structure. Fill every section; write "unknown — open question Qn" where needed.

```markdown
# <Product> — Project Context for CAD Agents
## 0. How to use this file
Read fully before modelling. Tags: [USER] beats [DESIGN] beats [RENDER]/[REF]; [VERIFY] = model as
written and list it in your report. Decisions go to DECISIONS_LOG.md (CAD-nnn).
## 1. Product identity and purpose
## 2. Source conflicts and how they were resolved
## 3. What the product must physically do (functions, motions with ranges, states)
## 4. Modelling brief: units, global coordinate system, folder and file names, custom properties,
##    colours/appearances, level of detail, required joints and their limits, configurations
## 5. Overall dimensions and stack-up (single source of truth, one table per axis)
## 6…N. One section per module: purpose, parts table (PN | part | material/process | key data),
##    interfaces to neighbours (exact coordinates), bought parts
## Bought parts master list (vendor, PN, CAD file, or "envelope")
## Fasteners and standards (holes to norm; FASTENER_LIST files)
## Clearance and function checks that must pass (numbered, with pass criteria)
## Build order and TRACKLIST (see below)
## Open questions
```

The tracklist is a table, kept current:

```markdown
| # | Module / step | Folder | Status | Evidence | Notes |
|---|---|---|---|---|---|
| 1 | Skeleton / layout | 00_Final_Assembly | done | SKEL box 600×1450×650, captures viewed | |
| 2 | Cabinet | 01_Cabinet | in progress | 12/18 parts | waiting Q3 |
| 3 | Door | 02_Door | todo | | |
```

Status values: `todo`, `in progress`, `blocked (reason)`, `done`. Only "done" when the module's assembly is
saved, its interference check has no unexplained hits, its checks from the checks section pass, and its captures
have been looked at.

Show the user a short summary (modules, build order, key `[USER]` values, open questions) and get a "go" before
modelling.

## Step 6: work against the context

- **Order:** the skeleton/layout first, then one module at a time in the tracklist order. Within a module: parts,
  then its assembly (skeleton first, parts at their global position and fixed, moving parts mated), then checks.
- **Before each module:** re-read its section and the neighbours' interface coordinates. Measure as-built
  neighbours (`inspect_model`, `select by="geometry"`) instead of trusting the spec when they differ, and log any
  difference.
- **Per feature:** compute the expected volume change by hand before the call and compare it with the
  reported delta. For a cut sketched inside material, pass `reverse_dir` explicitly and check the reported range.
- **Per part:** exact file name, material, custom properties, appearance, saved. Then `inspect_model` against the
  spec box and the neighbours, and a capture you have looked at.
- **Per assembly:**
  - `check_interference`, with every hit explained (by design, contact, or a fix);
  - drive every joint through its range;
  - run the module's checks, including envelope or sweep bodies for moving or falling parts at worst-case
    positions;
  - check against already-built neighbours in a scratch assembly that is never saved into the project.
- **Decisions:** you decide `[DESIGN]` gaps and append `CAD-nnn` with the reason. Stop and ask for `[USER]` values,
  the stack-up, or a failed hard requirement. Give the numbers and the options.
- **Keep the files current:**
  - Update the tracklist after every module or blocking event.
  - Append to DECISIONS_LOG.md.
  - Add new facts (as-built coordinates, new part numbers) to AGENT_CONTEXT.md, marked with their CAD-nnn.
  - Never silently rewrite a `[USER]` value.
- **Tool problems:** call `report_issue` straight away with the exact tool, arguments, full output, attempts,
  state, and expected vs actual. Then continue with a sound workaround, or stop if there is none. Don't hide
  problems with silent workarounds.
- **Memory and stability:**
  - one scratch assembly at a time;
  - close documents when done;
  - `restart_solidworks` between modules and on any MEMORY WARNING;
  - save before anything risky.
- **Report at each milestone:** what was built (paths, boxes, volumes, how verified), interference and check
  results, new decisions, `[VERIFY]` items, open questions, and the next step. The tracklist must match the report.

## Rules that save time

- Global coordinates everywhere: sketches take world points, and parts are inserted at 0,0,0 so their geometry is
  already in place.
- No claim without evidence: a number comes from `inspect_model` or a measurement, a look comes from a capture
  you opened.
- Don't model fasteners or other catalogue parts unless the user asks for that. Model holes, inserts and
  bosses to the norm sizes, and write `91_Fasteners/FASTENER_LIST_<module>.md` in the format from
  `solidworks-purchased-parts`, including where to get each part's CAD (TraceParts or the maker).
- Bought parts without CAD become orange envelope parts (`BUY_<Vendor>_<PN>_ENVELOPE`, property `Envelope = Yes`)
  with the real outer size and mounting features.
- When a context-window or session limit is near, save everything, update the tracklist, and write the next step
  into it, so a fresh agent can continue from the files alone.
