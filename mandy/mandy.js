"use strict";


/**
 * Sidebar class;
 * displays updates on the given div.
 */
class SideBar {
    constructor(jobName, div, terminal) {
        this.div = div;
        this.stepsdiv = document.createElement("div");
        this.jobs = {};
        this.steps = {};
        this.terminal = terminal;
        this.jobName = jobName;
        SideBar.clickedBuffer = null;
    }


    /**
     * Adds the job if it doesn't exist yet, or updates it.
     */
    jobUpdate(name, state, text) {
        var job = this.jobs[name];
        if (job === undefined) {
            job = {
                "elem": document.createElement("a"),
                "textNode": document.createTextNode(text)
            };
            job.elem.appendChild(document.createElement("b")).appendChild(document.createTextNode(name));
            job.elem.appendChild(document.createElement("br"));
            job.elem.appendChild(job.textNode);
            if (name !== this.jobName) {
                job.elem.href = (
                    document.location.protocol + "//" +
                    document.location.host + document.location.pathname +
                    "?wsurl=" + getEncodedParam("wsurl") +
                    "&project=" + getEncodedParam("project") +
                    "&hash=" + getEncodedParam("hash") +
                    "&job=" + name
                );
            } else {
                // show the step list inside this element.
                job.elem.appendChild(this.stepsdiv);
            }
            this.jobs[name] = job;
            this.div.appendChild(job.elem);
        } else {
            job.textNode.data = text;
        }

        if (state === "pending") {
            if (text.toLowerCase().startsWith("waiting")) {
                state = "waiting";
            } else {
                state = "running";
            }
        }

        job.elem.className = state;

        if (name === this.jobName) {
            job.elem.className += " active";
            document.title = "Build " + name + ": " + state + " (" + text + ")";
            document.getElementById("icon").href = "favicon-" + state + ".png";
        }
    }


    /**
     * Adds the step if it doesn't exist yet, or updates it.
     */
    stepUpdate(name, state, text) {
        var step = this.steps[name];
        if (step === undefined) {
            step = {
                "elem": document.createElement("a"),
                "textNode": document.createTextNode(text)
            };
            step.elem.appendChild(document.createElement("b")).appendChild(document.createTextNode(name));
            step.elem.appendChild(document.createElement("br"));
            step.elem.appendChild(step.textNode);
            step.elem.href = "#" + name;
            this.steps[name] = step;
            this.stepsdiv.appendChild(step.elem);
        } else {
            step.textNode.data = text;
        }

        if (state === "pending") {
            if (text.toLowerCase().startsWith("waiting")) {
                state = "waiting";
            } else {
                state = "running";
            }
        }
        if (state === "failure") {
            if (text.toLowerCase().startsWith("depends failed")) {
                state = "skipped";
            }
        }
        step.elem.className = "jobstate " + state;

        if (state !== "waiting") {
            this.terminal.addAnchor(name);
        }
    }
}


/**
 * initializes the mandy interface.
 *
 * arguments:
 *
 * con:
 *   the console div to be used.
 * ws:
 *   the websocket URL to be used.
 */
function mandyInit(term, sidebar, wsurl, jobName) {
    window.my_global_startperf = window.performance.now();
    console.log("milliseconds until loaded: ", window.my_global_startperf);

    var ws = new WebSocket(wsurl, "mandy");
    var state, text;
    ws.onmessage = function (event) {
        var msg = JSON.parse(event.data);
        if (msg.class === "StdOut") {
            term.write(msg.data);
        } else if (msg.class === "JobState") {
            var job_name = msg.job_name;
            state = msg.state;
            text = msg.text;
            sidebar.jobUpdate(job_name, state, text);

            if (msg.state === "success") {
                var perf = window.performance.now();
                console.log("milliseconds until done: ", perf - window.my_global_startperf);
            }
        } else if (msg.class === "StepState") {
            var step_name = msg.step_name;
            state = msg.state;
            text = msg.text;
            sidebar.stepUpdate(step_name, state, text);
        } else {
            console.log("received unknown update:");
            console.log(msg);
        }
    };
}


/**
 * Retrieves a parameter from the document's URL, in encoded form.
 */
function getEncodedParam(name) {
    name = name.replace(/[\[\]]/g, "\\$&");
    var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
        results = regex.exec(window.location.href);
    if (!results) {
        return null;
    }
    if (!results[2]) {
        return '';
    }
    return results[2].replace(/\+/g, " ");
}


/**
 * Run onload.
 */
window.addEventListener("load", function () {
    var jobName = getEncodedParam("job");
    var term = new Terminal(document.getElementById("terminal"));
    var sidebar = new SideBar(
        jobName,
        document.getElementById("sidebar"),
        term
    );

    var wsurl = (
        getEncodedParam("wsurl") +
        "?project=" +
        getEncodedParam("project") +
        "&hash=" +
        getEncodedParam("hash") +
        "&filter=" +
        jobName
    );

    console.log("websocket URL: ", wsurl);

    mandyInit(term, sidebar, wsurl, jobName);
});
