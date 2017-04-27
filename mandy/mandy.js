"use strict";


/**
 * Converts filesize in bytes to a human-readable string.
 */
function fileSizeToString(size) {
    if (size < 1024) {
        if (size == 1) {
            return "1 Byte";
        } else {
            return size.toString() + " Bytes";
        }
    } else {
        size /= 1024;
    }

    var suffix_id = 0;
    while (size >= 1024 && suffix_id < 3) {
        suffix_id += 1;
        size /= 1024;
    }
    return size.toFixed(1) + ["k", "M", "G", "T"][suffix_id] + "B";
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
 * Sidebar class;
 * displays updates on the given div.
 */
class SideBar {
    constructor(jobName, div, titleDiv, mandy) {
        this.div = div;
        this.titleDiv = titleDiv;
        this.stepsdiv = document.createElement("div");
        this.stepsdiv.className = "jobstates";
        this.jobs = {};
        this.steps = {};
        this.mostRecentlyUpdatedStep = null;
        this.mandy = mandy;
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
                    "&staticurl=" + getEncodedParam("staticurl") +
                    "&project=" + getEncodedParam("project") +
                    "&hash=" + getEncodedParam("hash") +
                    "&job=" + name
                );
                job.elem.appendChild(document.createElement("div")).className = "jobstates";
            } else {
                // show the step list inside this element.
                job.elem.appendChild(this.stepsdiv);
                // when clicking this element, the scroll mode is set to _end.
                job.elem.href = "#_end";
                job.elem.addEventListener("click", (e) => {
                    this.mandy.scrollMode = "_end";
                    this.mandy.autoScroll();
                });
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

        job.elem.className = "job " + state;

        if (name === this.jobName) {
            document.title = "Job " + name + ": " + state + " (" + text + ")";
        } else {
            job.elem.className += " inactive";
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
                "textNode": document.createTextNode(text),
                "outputItems": document.createElement("div")
            };
            step.elem.appendChild(document.createElement("b")).appendChild(document.createTextNode(name));
            step.elem.appendChild(document.createElement("br"));
            step.elem.appendChild(step.textNode);
            step.elem.appendChild(step.outputItems).className = "outputitems";
            step.elem.href = "#" + name;
            step.elem.addEventListener("click", (e) => {
                this.mandy.scrollMode = name;
                this.mandy.autoScroll();
                e.stopPropagation();
            });
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
            this.mandy.term.addAnchor(name);
        }

        this.mostRecentlyUpdatedStep = step;
    }


    /**
     * Adds an output item to the most recently-updated step.
     */
    outputItem(name, url, isdir, size) {
        var elem = document.createElement("a");
        elem.className = "outputitem";
        elem.appendChild(document.createElement("b")).appendChild(document.createTextNode(name));

        var infoString = fileSizeToString(size);
        if (isdir) {
            infoString += ", directory";
        }

        elem.appendChild(document.createTextNode(" (" + infoString + ")"));
        elem.href = url;
        this.mostRecentlyUpdatedStep.outputItems.appendChild(elem);
    }
}


/**
 * Main Mandy class.
 */
class Mandy {
    constructor() {
        var jobName = getEncodedParam("job");
        var project = getEncodedParam("project");
        var hash = getEncodedParam("hash");
        this.staticURL = getEncodedParam("staticurl") + project + "/jobs/" + hash.substr(0, 3) + "/" + hash.substr(3) + "/" + jobName + "/";
        this.term = new Terminal(document.getElementById("terminal"));
        this.sidebar = new SideBar(
            jobName,
            document.getElementById("sidebar"),
            document.getElementById("title"),
            this
        );

        // scrollMode describes the auto-scrolling target.
        // auto-scrolling engages whenever the terminal is updated or resized.
        // scrollMode string:
        //   scroll to the anchor given in the string.
        //   this is set from window.location.hash or when clicking a step in the sidebar.
        // scrollMode "_end":
        //   scroll automatically to the bottom of the document.
        //   this is set by default or when clicking the job in the sidebar.
        // scrollMode null:
        //   do not scroll automatically.
        //   this is set by the wheel event.
        if (window.location.hash.length > 1) {
            this.scrollMode = window.location.hash.substr(1);
        } else {
            this.scrollMode = "_end";
        }

        // disable
        document.addEventListener("wheel", (e) => { this.scrollMode = null; });

        this.sidebar.titleDiv.appendChild(document.createElement("b")).appendChild(document.createTextNode("Project: " + project));
        this.sidebar.titleDiv.appendChild(document.createElement("br"));
        this.sidebar.titleDiv.appendChild(document.createTextNode("Commit: " + hash.substr(0, 8)));
        this.sidebar.titleDiv.appendChild(document.createElement("br"));
        this.buildStatus = this.sidebar.titleDiv.appendChild(document.createTextNode("waiting for data from websocket..."));
        this.buildSourcesDiv = this.sidebar.titleDiv.appendChild(document.createElement("div"));
        this.buildSourcesDiv.className = "buildsources";

        var wsURL = (
            getEncodedParam("wsurl") +
            "?project=" +
            project +
            "&hash=" +
            hash +
            "&filter=" +
            jobName
        );
        this.ws = new WebSocket(wsURL, "mandy_v0");
        this.ws.onmessage = (e) => { this.onMessage(e); };
        this.ws.onerror = (e) => {
            if (e.target.readyState === 3) {
                this.error("Can't connect to Kevin.");
            } else {
                this.error("Websocket error.");
            }
        };

        window.my_global_startperf = window.performance.now();
    }


    /**
     * Show an error message in the sidebar.
     */
    error(msg) {
        this.buildStatus.data = "";
        var errMsg = document.createElement("div");
        errMsg.className = "errormessage";
        errMsg.appendChild(document.createTextNode(msg));
        this.sidebar.titleDiv.appendChild(errMsg);
    }


    /**
     * Invoked whenever automatic scrolling should be performed.
     */
    autoScroll() {
        if (this.scrollMode === null) {
            // auto-scrolling is disabled
            return;
        }

        var anchor = this.term.anchors[this.scrollMode];
        if (anchor === undefined) {
            // scroll to the bottom
            document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight;
        } else {
            // scroll to the anchor
            anchor.scrollIntoView();
        }
    }


    /**
     * Run whenever a websocket message is received.
     */
    onMessage(event) {
        var state, text;
        var msg = JSON.parse(event.data);
        if (msg.class === "StdOut") {
            this.term.write(msg.data);
            this.autoScroll();
        } else if (msg.class === "JobState") {
            var job_name = msg.job_name;
            state = msg.state;
            text = msg.text;
            this.sidebar.jobUpdate(job_name, state, text);
        } else if (msg.class === "StepState") {
            var step_name = msg.step_name;
            state = msg.state;
            text = msg.text;
            this.sidebar.stepUpdate(step_name, state, text);
        } else if (msg.class === "OutputItem") {
            this.sidebar.outputItem(msg.name, this.staticURL + msg.name, msg.isdir, msg.size);
        } else if (msg.class === "BuildSource") {
            var buildSource = document.createElement("a");
            buildSource.className = "buildsource";
            buildSource.text = msg.repo_url;
            buildSource.href = msg.repo_url;
            this.buildSourcesDiv.appendChild(buildSource);
        } else if (msg.class === "BuildState") {
            document.getElementById("icon").href = "favicon-" + msg.state + ".png";
            this.buildStatus.data = msg.text;
        } else if (msg.class == "RequestError") {
            this.error(msg.text);
        } else {
            console.log("unknown update", msg);
        }
    }
}


var mandy;
window.addEventListener("load", () => { mandy = new Mandy(); });
