"""Write the skill trigger eval sets in evals/skill-triggers/<skill>.json.

Each file is a list of {"query", "should_trigger"} items, the format skill-creator's description optimiser
(scripts.run_loop --eval-set) reads.

The negatives are hand-picked near misses: requests that belong to a sibling SolidWorks skill and that the
skill under test should NOT load for. Requests where two skills legitimately load together (a Toolbox bolt
needs assemblies too; a DIN bolt is also modeling) are left out of both sets, so the optimiser is never
pushed to narrow a description wrongly.

STATUS: draft, not yet reviewed by the maintainer and not yet run. Review the queries before optimising.

Run from the repository root:  python scripts/make_trigger_evals.py
"""

import json
from pathlib import Path

# Requests that should not load any SolidWorks skill.
UNRELATED = [
    "write a python script that renames all .sldprt files in a folder to lowercase",
    "explain the difference between von Mises and Tresca yield criteria",
    "model this bracket in Fusion 360",
    "what's the tap drill size for M10x1.5?",
]

SETS = {
    "solidworks-modeling": {
        "positive": [
            "model a 120x80x10 mm aluminium mounting plate with four M6 clearance holes 10 mm from each corner",
            "in solidworks, add a 2 mm fillet to all the outside edges of the bracket I have open",
            "make the wall of this enclosure 3 mm instead of 2, it's a shell feature I think",
            "can you cut a 20 mm wide slot through the middle of the part, along X",
            "desenha uma chapa metálica em L com 1.5 mm de espessura e abas de 40 e 25 mm",
            "create a shaft Ø20 x 150 with a 1x45 chamfer on both ends and a keyway",
            "the part I have open has 3 bodies but should be one, figure out why and fix it",
            "revolve a pulley profile: OD 60, bore 12, groove 8 mm wide",
        ],
        "negative": [
            "export the plate drawing as DXF for the laser cutter",
            "check if anything collides in the gearbox assembly",
            "will this bracket hold 500 N hanging from the end?",
            "SolidWorks is stuck, there's some dialog open and every tool times out",
            "show me sliders so I can play with the size of this part",
            "insert four M8x30 ISO 4017 bolts from the Toolbox into the flange holes",
        ],
    },
    "solidworks-assemblies": {
        "positive": [
            "put the motor STEP from the purchased folder into the frame assembly and mate the shaft to the coupler",
            "add a concentric mate between the pin and the hinge hole",
            "check if anything collides in the gearbox assembly",
            "drive the lid hinge from 0 to 110 degrees and tell me where it hits the body",
            "criar montagem com a base e o suporte, o suporte fixo em cima da base",
            "the distance mate put the slider on the wrong side, flip it",
            "make the door subassembly flexible so the hinge can move in the top assembly",
            "save an 'open' and a 'closed' state of the clamp mechanism",
        ],
        "negative": [
            "add a 2 mm fillet to all the outside edges of the bracket",
            "make a manufacturing drawing of the bracket on A3",
            "I need a DIN 933 M8x40 bolt as a part file",
            "run a static stress analysis on the part with 2 MPa pressure",
            "is this bracket printable on an FDM printer without supports",
        ],
    },
    "solidworks-drawings": {
        "positive": [
            "make a manufacturing drawing of the bracket on A3 with front, top, right and iso views",
            "add a section view A-A through the middle of the housing drawing",
            "export the plate drawing as DXF for the laser cutter",
            "insert a parts-only BOM on the assembly drawing",
            "criar desenho técnico da peça com cotas e exportar em PDF",
            "the workshop needs a PDF with all dimensions of the shaft",
            "put the model dimensions into the drawing views",
            "make a drawing of the assembly with a bill of materials, A2",
        ],
        "negative": [
            "capture an isometric screenshot of the part",
            "cut a 20 mm wide slot through the middle of the part",
            "check if anything collides in the gearbox assembly",
            "show me sliders so I can play with the size of this part",
        ],
    },
    "solidworks-simulation": {
        "positive": [
            "will this bracket hold 500 N hanging from the end? it's fixed by the two bolt holes",
            "run a static stress analysis on the part with 2 MPa pressure on the inside face",
            "what's the factor of safety of the lever in 6061-T6",
            "how much does the cantilever bend under 200 N at the tip",
            "análise de tensões da peça aberta com 1000 N na face de cima",
            "find the first natural frequency of the plate",
            "mesh finer and re-run the study, the stress looks off",
            "is 3 mm steel enough for this hook if I hang 80 kg on it",
        ],
        "negative": [
            "explain the difference between von Mises and Tresca yield criteria",
            "check if anything collides in the gearbox assembly",
            "is this bracket printable on an FDM printer without supports",
            "set the material of the part to AISI 304 and give me its mass",
        ],
    },
    "solidworks-din-parts": {
        "positive": [
            "I need a DIN 933 M8x40 bolt as a part file, we can't download CAD here",
            "model an M16 hex nut to ISO 4032",
            "build the DN50 PN6 welding flange to DIN 2573 yourself, don't use a catalogue",
            "modela uma anilha DIN 125 para M10",
            "model the washers and nuts for M12 in standard sizes as separate parts",
            "desenha a porca sextavada M20 numa peça nova",
            "model an ISO 4014 M10x80 partly threaded bolt",
            "draw me a DIN 127 spring washer for M8 as a solidworks part",
        ],
        "negative": [
            "add M8 bolts and nuts to hold the flange to the housing",
            "insert four M8x30 ISO 4017 bolts from the Toolbox into the flange holes",
            "the shaft needs a 6204 bearing in the housing",
            "add a counterbored M6 hole to the plate",
            "what's the tap drill size for M10x1.5?",
        ],
    },
    "solidworks-purchased-parts": {
        "positive": [
            "add M8 bolts and nuts to hold the flange to the housing",
            "the shaft needs a 6204 bearing in the housing",
            "what fasteners does this assembly need? make me a list",
            "put screws in the four corner holes of the cover",
            "lista de parafusos e anilhas para a montagem da tampa",
            "we need a NEMA 17 stepper on this bracket",
            "add the linear guide rails and carriages to the frame",
            "I downloaded the bolts from traceparts, they're in 91_Fasteners, put them in",
        ],
        "negative": [
            "model an M16 hex nut to ISO 4032",
            "insert four M8x30 ISO 4017 bolts from the Toolbox into the flange holes",
            "add a counterbored M6 hole to the plate",
            "explain the difference between von Mises and Tresca yield criteria",
        ],
    },
    "solidworks-toolbox": {
        "positive": [
            "insert four M8x30 ISO 4017 bolts from the Toolbox into the flange holes",
            "add standard washers from the library under each screw head",
            "use toolbox screws for the cover, M5 socket heads",
            "switch the Toolbox screw in the assembly from M6x20 to M6x25",
            "inserir parafusos da Toolbox nos furos da tampa",
            "load the Toolbox add-in and put a 6204 bearing on the shaft",
            "pick an ISO 4762 M6x20 from the toolbox library",
        ],
        "negative": [
            "I need a DIN 933 M8x40 bolt as a part file, we can't download CAD here",
            "add M8 bolts and nuts to hold the flange to the housing",
            "model an M16 hex nut to ISO 4032",
            "add a concentric mate between the pin and the hinge hole",
            "add a counterbored M6 hole to the plate",
        ],
    },
    "solidworks-diagnostics": {
        "positive": [
            "SolidWorks is stuck, there's some dialog open and every tool times out",
            "the last tool returned swModalDialogBlocking, deal with it",
            "undo the last three features, they broke the model",
            "the fillet shows a red X in the tree, what's wrong",
            "there are like 4 SLDWORKS.exe processes running and the PC is slow",
            "every call now says swWorkerBusy",
            "load the simulation add-in, it says it is not available",
            "o SolidWorks bloqueou, diagnosticar erros",
        ],
        "negative": [
            "add a 2 mm fillet to all the outside edges of the bracket",
            "check if anything collides in the gearbox assembly",
            "the distance mate put the slider on the wrong side, flip it",
            "write a python script that renames all .sldprt files in a folder to lowercase",
        ],
    },
    "solidworks-dfm": {
        "positive": [
            "can this pocketed part be machined on a 3-axis mill with a 6 mm cutter?",
            "check the enclosure for injection moulding problems: draft, wall thickness, ribs",
            "is this bracket printable on an FDM printer without supports",
            "review the sheet metal part, are the holes too close to the bends",
            "verificar fabricabilidade da peça para CNC",
            "what bend radius should I use for 2 mm stainless",
            "make this housing moldable, it has no draft",
            "is a 0.8 mm wall ok for SLS nylon",
        ],
        "negative": [
            "will this bracket hold 500 N hanging from the end?",
            "export the plate drawing as DXF for the laser cutter",
            "make the wall of this enclosure 3 mm instead of 2",
            "check if anything collides in the gearbox assembly",
        ],
    },
    "solidworks-interactive": {
        "positive": [
            "show me sliders so I can play with the size of this part",
            "let me adjust the bolt's dimensions visually",
            "open the interactive studio for the model",
            "I have adjusted the SolidWorks model parameters via the interactive studio:\n"
            "- Head Thickness changed from 4.8 to 6 mm",
            "I want to try different widths and see how it looks",
            "estúdio interativo para ajustar as dimensões",
        ],
        "negative": [
            "make the bracket 60 mm long",
            "list all the dimensions of the part",
            "capture an isometric screenshot of the part",
            "add a global variable Width and drive both pockets from it",
        ],
    },
    "solidworks-project": {
        "positive": [
            "let's model the whole coffee machine, the spec and renders are in D:/CAD/Brewer",
            "continue the CAD project, read the context file first",
            "I want to build the complete product in SolidWorks, about 40 parts, where do we start",
            "continuar o projeto da máquina, pasta C:/Projetos/Maquina",
            "set up AGENT_CONTEXT.md for my robot arm project",
            "pick up where the last agent stopped on the fridge model",
        ],
        "negative": [
            "put the motor STEP into the frame assembly and mate the shaft to the coupler",
            "model a 120x80x10 mm aluminium mounting plate with four M6 holes",
            "make a manufacturing drawing of the bracket on A3",
            "check if anything collides in the gearbox assembly",
        ],
    },
    "solidworks-welcome-demo": {
        "positive": [
            "I just installed the SolidWorks plugin and restarted, show me it works",
            "run the welcome demo",
            "test the solidworks connection with a quick demo part",
            "is the solidworks mcp working? do something simple to prove it",
        ],
        "negative": [
            "SolidWorks is stuck, there's some dialog open and every tool times out",
            "model a 120x80x10 mm aluminium mounting plate",
            "show me sliders so I can play with the size of this part",
        ],
    },
}


def main() -> None:
    out = Path("evals/skill-triggers")
    out.mkdir(parents=True, exist_ok=True)
    for skill, s in SETS.items():
        negatives = s["negative"] + UNRELATED
        items = [{"query": q, "should_trigger": True} for q in s["positive"]]
        items += [{"query": q, "should_trigger": False} for q in negatives]
        (out / f"{skill}.json").write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{skill}: {len(s['positive'])} should trigger, {len(negatives)} should not")


if __name__ == "__main__":
    main()
