import os
import sys
import json
import subprocess
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, abort, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = BASE_DIR / "docs"
LF_DATA_DIR = BASE_DIR / "LipidFinder" / "Data"
CONFIG_DIR = BASE_DIR / "webapp" / "config"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = CONFIG_DIR / "state.json"

app = Flask(__name__)
app.secret_key = "lipidfinder-webui-secret"


def load_template():
    template_path = LF_DATA_DIR / "parameters_template.json"
    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)


def module_params(module):
    tpl = load_template()
    filtered = {}
    for key, spec in tpl.items():
        if module in spec.get("modules", []):
            filtered[key] = spec
    return filtered


def read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def update_state(**kwargs):
    state = read_state()
    for k, v in kwargs.items():
        if v is not None:
            state[k] = v
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        # Best-effort persistence; ignore failures
        pass


def default_value_for(spec):
    t = spec.get("type")
    if t == "bool":
        return bool(spec.get("default", False))
    if t == "path":
        return spec.get("default", "")
    if t in ["str", "int", "float", "selection"]:
        return spec.get("default")
    if t in ["int range", "float range"]:
        return spec.get("default", [None, None])
    if t in ["multiselection", "pairs"]:
        return spec.get("default", [])
    return spec.get("default")


@app.route("/")
def index():
    saved = {
        "peakfilter": (CONFIG_DIR / "peakfilter.json").exists(),
        "amalgamator": (CONFIG_DIR / "amalgamator.json").exists(),
        "mssearch": (CONFIG_DIR / "mssearch.json").exists(),
    }
    return render_template("index.html", saved=saved)


@app.route("/params/<module>", methods=["GET", "POST"])
def params(module):
    module = module.lower()
    if module not in ["peakfilter", "amalgamator", "mssearch"]:
        flash("Unknown module")
        return redirect(url_for("index"))
    specs = module_params(module)
    values = {}
    saved_path = CONFIG_DIR / f"{module}.json"
    if request.method == "POST":
        # Collect values from form
        for key, spec in specs.items():
            typ = spec.get("type")
            if typ == "bool":
                values[key] = bool(request.form.get(key))
            elif typ in ["int", "float"]:
                raw = request.form.get(key)
                if raw is None or raw == "":
                    values[key] = None
                else:
                    values[key] = int(raw) if typ == "int" else float(raw)
            elif typ == "selection":
                values[key] = request.form.get(key)
            elif typ == "path":
                values[key] = request.form.get(key)
            elif typ in ["str"]:
                values[key] = request.form.get(key)
            elif typ in ["int range", "float range"]:
                a = request.form.get(f"{key}_a")
                b = request.form.get(f"{key}_b")
                def cast(v):
                    if v is None or v == "":
                        return None
                    return int(v) if typ == "int range" else float(v)
                values[key] = [cast(a), cast(b)]
            elif typ == "pairs":
                # Expect lines like: val1|val2, val3|val4
                raw = request.form.get(key, "")
                pairs = []
                for chunk in raw.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    parts = [p.strip() for p in chunk.split("|")]
                    if len(parts) == 2:
                        pairs.append(parts)
                values[key] = pairs
            else:
                values[key] = request.form.get(key)

        # Save only active module parameters
        with open(saved_path, "w", encoding="utf-8") as f:
            json.dump(values, f, indent=4)
        flash(f"Saved parameters for {module} to {saved_path}")
        return redirect(url_for("index"))

    # Prefill from saved config if present
    if saved_path.exists():
        try:
            with open(saved_path, "r", encoding="utf-8") as f:
                saved_values = json.load(f)
        except Exception:
            saved_values = {}
    else:
        saved_values = {}

    # Build initial values
    for key, spec in specs.items():
        values[key] = saved_values.get(key, default_value_for(spec))

    return render_template("params.html", module=module, specs=specs, values=values)


@app.route("/xcms", methods=["GET", "POST"])
def xcms():
    if request.method == "POST":
        rscript_path = request.form.get("rscript_path", "Rscript")
        mzxml_dir = request.form.get("mzxml_dir", "")
        report_name = request.form.get("report_name", "xcms_report")
        if not mzxml_dir:
            flash("Please provide the mzXML directory path.")
            return redirect(url_for("xcms"))
        # Run R script and pass two inputs via stdin
        script_path = str(DOCS_DIR / "xcms.R")
        try:
            proc = subprocess.run(
                [rscript_path, script_path],
                input=f"{mzxml_dir}\n{report_name}\n".encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(BASE_DIR),
                check=False,
            )
        except FileNotFoundError:
            flash("Rscript not found. Please set the correct Rscript path.")
            return redirect(url_for("xcms"))

        out = proc.stdout.decode(errors="ignore")
        err = proc.stderr.decode(errors="ignore")
        # Check output file exists
        result_path = Path(mzxml_dir) / f"{report_name}.csv"
        success = result_path.exists()
        if success:
            # Persist last produced XCMS CSV for automation in Pipeline
            rp = str(result_path)
            key = None
            lower = rp.lower()
            if "neg" in lower or "negative" in lower:
                key = "xcms_last_negative_csv"
            elif "pos" in lower or "positive" in lower:
                key = "xcms_last_positive_csv"
            update_state(xcms_last_csv=rp, **({key: rp} if key else {}))
        return render_template(
            "run_result.html",
            title="XCMS Run",
            command=f"{rscript_path} {script_path}",
            success=success,
            result_path=str(result_path),
            stdout=out,
            stderr=err,
        )

    return render_template("xcms.html")


@app.route("/browse_dir", methods=["GET"])
def browse_dir():
    """Open a local directory picker and return the selected path.
    Uses Windows PowerShell FolderBrowserDialog to avoid tkinter issues under debug.
    Only works when running the server locally on Windows.
    """
    try:
        ps_command = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$fbd = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$fbd.Description = 'Select mzXML directory'; "
            "$null = $fbd.ShowDialog(); "
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Write-Output $fbd.SelectedPath"
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(BASE_DIR),
            check=False,
        )
        out = proc.stdout.decode(errors="ignore").strip()
        err = proc.stderr.decode(errors="ignore").strip()
        if proc.returncode != 0:
            raise RuntimeError(err or "Folder dialog cancelled or failed")
        return jsonify({"path": out})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_python_module(module_name, args_list):
    cmd = [sys.executable, "-m", module_name] + args_list
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(BASE_DIR),
        check=False,
    )
    return cmd, proc


@app.route("/pipeline", methods=["GET"])
def pipeline():
    # Show forms to run PeakFilter, Amalgamator, MSSearch
    saved = {
        "peakfilter": str(CONFIG_DIR / "peakfilter.json"),
        "amalgamator": str(CONFIG_DIR / "amalgamator.json"),
        "mssearch": str(CONFIG_DIR / "mssearch.json"),
    }
    return render_template("pipeline.html", saved=saved)


@app.route("/run/peakfilter", methods=["POST"])
def run_peakfilter():
    input_path = request.form.get("input_path", "")
    output_dir = request.form.get("output_dir", "")
    params_path = request.form.get("params_path", str(CONFIG_DIR / "peakfilter.json"))
    verbose = bool(request.form.get("verbose"))
    timestamp = bool(request.form.get("timestamp"))
    if not input_path:
        # Try to auto-resolve from last XCMS run
        st = read_state()
        candidates = [
            st.get("xcms_last_negative_csv"),
            st.get("xcms_last_positive_csv"),
            st.get("xcms_last_csv"),
        ]
        for cand in candidates:
            if cand and os.path.isfile(cand):
                input_path = cand
                break
        if not input_path:
            flash("No input provided and no XCMS output found. Run XCMS first.")
            return redirect(url_for("pipeline"))
    if not os.path.isfile(params_path):
        flash("PeakFilter parameters not found. Configure them under Params.")
        return redirect(url_for("pipeline"))
    args = ["-i", input_path, "-p", params_path]
    if output_dir:
        args += ["-o", output_dir]
    if verbose:
        args += ["--verbose"]
    if timestamp:
        args += ["--timestamp"]
    cmd, proc = run_python_module("LipidFinder.run_peakfilter", args)
    out = proc.stdout.decode(errors="ignore")
    err = proc.stderr.decode(errors="ignore")
    success = proc.returncode == 0
    # Persist last PeakFilter summary hints if possible
    try:
        lower_in = Path(input_path).name.lower()
        if output_dir:
            if ("neg" in lower_in) or ("negative" in lower_in):
                update_state(peakfilter_last_negative_summary=os.path.join(output_dir, "peakfilter_negative_summary.csv"))
            elif ("pos" in lower_in) or ("positive" in lower_in):
                update_state(peakfilter_last_positive_summary=os.path.join(output_dir, "peakfilter_positive_summary.csv"))
            else:
                # Unknown polarity; generic summary
                update_state(peakfilter_last_summary=os.path.join(output_dir, "peakfilter_summary.csv"))
    except Exception:
        pass
    return render_template(
        "run_result.html",
        title="PeakFilter Run",
        command=" ".join(cmd),
        success=success,
        result_path=output_dir,
        stdout=out,
        stderr=err,
    )


@app.route("/run/amalgamator", methods=["POST"])
def run_amalgamator():
    neg_file = request.form.get("neg_file", "")
    pos_file = request.form.get("pos_file", "")
    output_dir = request.form.get("output_dir", "")
    params_path = request.form.get("params_path", str(CONFIG_DIR / "amalgamator.json"))
    if not (neg_file and pos_file):
        # Try to auto-resolve from last PeakFilter run(s)
        st = read_state()
        neg_file = neg_file or st.get("peakfilter_last_negative_summary")
        pos_file = pos_file or st.get("peakfilter_last_positive_summary")
        if not (neg_file and pos_file):
            flash("Missing Amalgamator inputs. Run PeakFilter for both polarities first.")
            return redirect(url_for("pipeline"))
    if not os.path.isfile(params_path):
        flash("Amalgamator parameters not found. Configure them under Params.")
        return redirect(url_for("pipeline"))
    args = ["-neg", neg_file, "-pos", pos_file, "-p", params_path]
    if output_dir:
        args += ["-o", output_dir]
    cmd, proc = run_python_module("LipidFinder.run_amalgamator", args)
    out = proc.stdout.decode(errors="ignore")
    err = proc.stderr.decode(errors="ignore")
    success = proc.returncode == 0
    if success and output_dir:
        update_state(amalgamator_last_csv=os.path.join(output_dir, "amalgamated.csv"))
    return render_template(
        "run_result.html",
        title="Amalgamator Run",
        command=" ".join(cmd),
        success=success,
        result_path=output_dir,
        stdout=out,
        stderr=err,
    )


@app.route("/run/mssearch", methods=["POST"])
def run_mssearch():
    input_file = request.form.get("input_file", "")
    output_dir = request.form.get("output_dir", "")
    params_path = request.form.get("params_path", str(CONFIG_DIR / "mssearch.json"))
    if not input_file:
        # Try to auto-resolve from last Amalgamator or PeakFilter
        st = read_state()
        input_file = st.get("amalgamator_last_csv") or st.get("peakfilter_last_negative_summary") or st.get("peakfilter_last_positive_summary") or st.get("peakfilter_last_summary")
        if not input_file:
            flash("Missing MSSearch input. Run Amalgamator or PeakFilter first.")
            return redirect(url_for("pipeline"))
    if not os.path.isfile(params_path):
        flash("MSSearch parameters not found. Configure them under Params.")
        return redirect(url_for("pipeline"))
    args = ["-i", input_file, "-p", params_path]
    if output_dir:
        args += ["-o", output_dir]
    cmd, proc = run_python_module("LipidFinder.run_mssearch", args)
    out = proc.stdout.decode(errors="ignore")
    err = proc.stderr.decode(errors="ignore")
    success = proc.returncode == 0
    return render_template(
        "run_result.html",
        title="MSSearch Run",
        command=" ".join(cmd),
        success=success,
        result_path=output_dir,
        stdout=out,
        stderr=err,
    )


@app.route("/run/pipeline_full", methods=["POST"])
def run_pipeline_full():
    # Gather inputs
    neg_input = request.form.get("neg_input", "")
    pos_input = request.form.get("pos_input", "")
    peakfilter_neg_params = str(CONFIG_DIR / "peakfilter.json")
    peakfilter_pos_params = str(CONFIG_DIR / "peakfilter.json")
    amalgamator_params = str(CONFIG_DIR / "amalgamator.json")
    mssearch_params = str(CONFIG_DIR / "mssearch.json")
    output_dir = request.form.get("output_dir", "")
    verbose = bool(request.form.get("verbose"))
    timestamp = bool(request.form.get("timestamp"))

    if not output_dir:
        flash("Please provide an output directory for the pipeline.")
        return redirect(url_for("pipeline"))
    if not (neg_input or pos_input):
        # Auto-resolve from last XCMS run
        st = read_state()
        # Prefer polarity-specific CSVs; fall back to generic
        neg_input = st.get("xcms_last_negative_csv") or neg_input
        pos_input = st.get("xcms_last_positive_csv") or pos_input
        if not (neg_input or pos_input):
            generic = st.get("xcms_last_csv")
            if generic and os.path.isfile(generic):
                lower = Path(generic).name.lower()
                if ("neg" in lower) or ("negative" in lower):
                    neg_input = generic
                elif ("pos" in lower) or ("positive" in lower):
                    pos_input = generic
                else:
                    # Unknown polarity; run as single input (negative by default)
                    neg_input = generic
        if not (neg_input or pos_input):
            flash("No inputs found. Run XCMS first to generate aligned CSVs.")
            return redirect(url_for("pipeline"))
    # Ensure required parameter files exist
    missing = []
    if neg_input or pos_input:
        if not os.path.isfile(peakfilter_neg_params):
            missing.append("PeakFilter")
    # Amalgamator required only if both summaries will be combined
    # We still check presence to provide early feedback
    if not os.path.isfile(amalgamator_params):
        missing.append("Amalgamator")
    if not os.path.isfile(mssearch_params):
        missing.append("MSSearch")
    if missing:
        flash("Missing parameters for: " + ", ".join(missing) + ". Configure them under Params.")
        return redirect(url_for("pipeline"))

    logs = []
    def log_entry(title, cmd, proc):
        return {
            "title": title,
            "command": " ".join(cmd),
            "stdout": proc.stdout.decode(errors="ignore"),
            "stderr": proc.stderr.decode(errors="ignore"),
            "success": proc.returncode == 0,
        }

    # 1) Run PeakFilter for provided inputs
    neg_summary = None
    pos_summary = None
    if neg_input:
        args = ["-i", neg_input, "-p", peakfilter_neg_params, "-o", output_dir]
        if verbose:
            args += ["--verbose"]
        if timestamp:
            args += ["--timestamp"]
        cmd, proc = run_python_module("LipidFinder.run_peakfilter", args)
        logs.append(log_entry("PeakFilter (negative)", cmd, proc))
        if proc.returncode != 0:
            return render_template("pipeline_visualize.html", success=False, output_dir=output_dir, logs=logs)
        neg_summary = os.path.join(output_dir, "peakfilter_negative_summary.csv")
    if pos_input:
        args = ["-i", pos_input, "-p", peakfilter_pos_params, "-o", output_dir]
        if verbose:
            args += ["--verbose"]
        if timestamp:
            args += ["--timestamp"]
        cmd, proc = run_python_module("LipidFinder.run_peakfilter", args)
        logs.append(log_entry("PeakFilter (positive)", cmd, proc))
        if proc.returncode != 0:
            return render_template("pipeline_visualize.html", success=False, output_dir=output_dir, logs=logs)
        pos_summary = os.path.join(output_dir, "peakfilter_positive_summary.csv")

    # 2) Amalgamate if both summaries exist
    amalgamated_csv = None
    if neg_summary and pos_summary:
        args = ["-neg", neg_summary, "-pos", pos_summary, "-p", amalgamator_params, "-o", output_dir]
        cmd, proc = run_python_module("LipidFinder.run_amalgamator", args)
        logs.append(log_entry("Amalgamator", cmd, proc))
        if proc.returncode != 0:
            return render_template("pipeline_visualize.html", success=False, output_dir=output_dir, logs=logs)
        amalgamated_csv = os.path.join(output_dir, "amalgamated.csv")

    # 3) MSSearch on amalgamated or single summary
    ms_input = amalgamated_csv or neg_summary or pos_summary
    # Ensure plots are in PNG for web
    try:
        with open(mssearch_params, "r", encoding="utf-8") as f:
            ms_params_obj = json.load(f)
    except Exception:
        ms_params_obj = {}
    ms_params_obj.setdefault("plotCategories", True)
    ms_params_obj.setdefault("summary", True)
    ms_params_obj["figFormat"] = "png"
    tmp_ms_params = CONFIG_DIR / "mssearch_web_pipeline.json"
    with open(tmp_ms_params, "w", encoding="utf-8") as f:
        json.dump(ms_params_obj, f, indent=2)

    args = ["-i", ms_input, "-p", str(tmp_ms_params), "-o", output_dir]
    cmd, proc = run_python_module("LipidFinder.run_mssearch", args)
    logs.append(log_entry("MSSearch", cmd, proc))
    if proc.returncode != 0:
        return render_template("pipeline_visualize.html", success=False, output_dir=output_dir, logs=logs)

    # Build visualization data
    db_name = (ms_params_obj.get("database") or "all_lmsd").lower()
    summary_xlsx = os.path.join(output_dir, f"mssearch_{db_name}_summary.xlsx")
    scatter_png = os.path.join(output_dir, f"category_scatterplot_{db_name}.png")
    full_xlsx = os.path.join(output_dir, f"mssearch_{db_name}.xlsx")

    category_counts = {}
    try:
        import pandas as pd
        if os.path.isfile(summary_xlsx):
            df = pd.read_excel(summary_xlsx)
            if "Category" in df.columns:
                counts = df["Category"].value_counts()
                category_counts = {str(k): int(v) for k, v in counts.items()}
    except Exception:
        category_counts = {}

    return render_template(
        "pipeline_visualize.html",
        success=True,
        output_dir=output_dir,
        logs=logs,
        scatter_png=scatter_png if os.path.isfile(scatter_png) else None,
        summary_xlsx=summary_xlsx if os.path.isfile(summary_xlsx) else None,
        full_xlsx=full_xlsx if os.path.isfile(full_xlsx) else None,
        amalgamated_csv=amalgamated_csv if amalgamated_csv and os.path.isfile(amalgamated_csv) else None,
        category_counts=category_counts,
    )


@app.route("/files")
def serve_file():
    path = request.args.get("path")
    if not path or not os.path.isfile(path):
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=True)