"use strict";


/**
 * Terminal class;
 * renders text received via write() to the given div,
 * respecting ANSI SGR codes.
 */
class Terminal {
    constructor(div) {
        // the terminal div
        this.div = div;
        this.div.className = "terminal";
        this.anchors = {};

        this.unfinishedSequence = null;

        this.bold = false;
        this.italic = false;
        this.underline = false;
        this.fgcol = null;
        this.bgcol = null;

        // this flag is set when a \r is received.
        // if the flag is set, then when printing a new character the current line is erased.
        // on \n, the flag is cleared.
        this.CRactive = false;
    }


    write(text) {
        if (this.unfinishedSequence !== null) {
            // there was an unfinished escape sequence from a previous invocation.
            text = '\x1b' + this.unfinishedSequence + text;
        }

        var parts = text.split('\x1b');

        // parts[0] is a plain string.
        // parts[1:] start with an escape sequence each.

        this.writeRaw(parts[0]);

        for (let i = 1; i < parts.length; i++) {
            if (this.unfinishedSequence !== null) {
                // the previous part was unfinished, but we've already found a new part.
                // wat?
                // I guess we should print the previous part because something was wrong with it.
                this.writeRaw(this.unfinishedSequence);
            }
            var part = parts[i];

            if (part[0] === '[') {
                // a CSI code.
                this.unfinishedSequence = part;
                // this sequence is terminated by a character c with "@" <= c <= "~".
                // search for that character.
                for (let j = 1; j < part.length; j++) {
                    if (("@" <= part[j]) && (part[j] <= "~")) {
                        this.CSICode(part.slice(1, j + 1));
                        this.writeRaw(part.slice(j + 1));
                        this.unfinishedSequence = null;
                        break;
                    }
                }
            } else {
                // a non-CSI escape.
                // ignore and print it as-is, as we don't know how this is
                // terminated.
                console.log("unimplemented non-CSI escape: " + part[0]);
                this.writeRaw(part);
            }
        }

        this.unfinishedSequence = null;
    }


    /**
     * Called when a \r character is encountered.
     * Removes all contents of the current line, effectively placing the "cursor"
     * at the start of the line.
     *
     * This is not exactly the same behavior as in a real terminal because
     * a real terminal wouldn't erase the line, just move the cursor back.
     *
     * However, fuck it.
     */
    removeLastLine() {
        while (true) {
            if (this.div.lastChild === null) {
                // looks like there's nothing left but ashes.
                break;
            }
            if (this.div.lastChild.tagName != "SPAN") {
                // this is not a SPAN element;
                // probably it's an anchor or some other element we'd rather not touch.
                break;
            }
            var textNode = this.div.lastChild.firstChild;
            var newlinePos = textNode.data.lastIndexOf('\n');
            if (newlinePos == -1) {
                // this span has no newline; discard it entirely.
                this.div.removeChild(this.div.lastChild);
            } else {
                // discard the text after the last newline.
                textNode.data = textNode.data.slice(0, newlinePos + 1);
                break;
            }
        }
    }


    /**
     * Adds some raw text in the current style setting, ignoring escape sequences.
     * Does not take care of scrolling.
     *
     * The text may still contain ASCII control characters such as \r and \n.
     */
    writeRaw(rawText) {
        // performance optimization: remove unneeded instances of \r,
        // to avoid the extra handling in the following loop.
        var text = rawText.replace("\r\n", "\n");

        while (true) {
            if (text === "") {
                return;
            }

            var nextCR = text.indexOf("\r");

            if (nextCR == -1) {
                // the remainder of text can be printed as-is.
                break;
            }

            // print the slice of text up until the \r.
            // the slice is guaranteed \r-free, which helps to avoid endless recursion and such.
            this.writeRaw(text.slice(0, nextCR));

            // set the \r-encountered flag
            this.CRActive = true;

            // repeat processing
            text = text.slice(nextCR + 1);
        }

        if (text[0] == "\n") {
            // remove the \r-encountered flag
            this.CRActive = false;
        }

        if (this.CRActive) {
            // looks like there was a \r which now forces us to clear the current line.
            this.removeLastLine();
        }

        var classes = "";

        function addClass(name) {
            if (classes === "") {
                classes = name;
            } else {
                classes = classes + " " + name;
            }
        }

        if (this.bold) { addClass("bold"); }
        if (this.italic) { addClass("italic"); }
        if (this.underline) { addClass("underline"); }
        if (this.bgcol !== null) { addClass(this.bgcol); }
        if (this.fgcol !== null) { addClass(this.fgcol); }

        this.addToDOM(text, classes);
    }


    addToDOM(text, classes) {
        if ((this.div.lastChild !== null) && (this.div.lastChild.className === classes)) {
            this.div.lastChild.firstChild.data += text;
        } else {
            var newSpan = document.createElement("span");
            newSpan.className = classes;
            newSpan.appendChild(document.createTextNode(text));
            this.div.appendChild(newSpan);
        }
    }


    addAnchor(anchorName) {
        if (anchorName in this.anchors) {
            // this anchor does already exist.
            return;
        }
        var anchorElement = document.createElement("a");
        anchorElement.name = anchorName;
        anchorElement.className = "anchor";
        anchorElement.appendChild(document.createTextNode("anchor: #" + anchorName));
        this.div.appendChild(anchorElement);
        this.anchors[anchorName] = anchorElement;

        if (document.location.hash === "#" + anchorName) {
            // jump to the anchor, if it has not existed yet
            document.location.hash = document.location.hash;
        }
    }


    /**
     * Is called whenever a CSI sequence, such as '31;1m', is encountered.
     * Changes the internal state of the terminal accordingly.
     */
    CSICode(csi) {
        var code = csi.slice(-1);

        if (code == "m") {
            this.SGRCode(csi.slice(0, -1));
        } else {
            // some unknown code. NOP.
            console.log("unimplemented CSI code: " + csi);
        }
    }


    /**
     * Is called whenever a SGR code, such as '31;1', is encountered.
     * Changes the internal state of the terminal accordingly.
     */
    SGRCode(sgr) {
        if (sgr === "") {
            sgr = "0";
        }

        for (let code of sgr.split(';')) {
            code = parseInt(code, 10);
            if (code === 0)  {
                this.bold = false;
                this.italic = false;
                this.underline = false;
                this.bgcol = null;
                this.fgcol = null;
            }
            else if (code === 1)  { this.bold = true; }
            else if (code === 21) { this.bold = false; }
            else if (code === 3)  { this.italic = true; }
            else if (code === 23) { this.italic = false; }
            else if (code === 4)  { this.underline = true; }
            else if (code === 24) { this.underline = false; }
            else if (code === 30) { this.fgcol = "col0"; }
            else if (code === 31) { this.fgcol = "col1"; }
            else if (code === 32) { this.fgcol = "col2"; }
            else if (code === 33) { this.fgcol = "col3"; }
            else if (code === 34) { this.fgcol = "col4"; }
            else if (code === 35) { this.fgcol = "col5"; }
            else if (code === 36) { this.fgcol = "col6"; }
            else if (code === 37) { this.fgcol = "col7"; }
            else if (code === 39) { this.fgcol = null; }
            else if (code === 40) { this.bgcol = "bgcol0"; }
            else if (code === 41) { this.bgcol = "bgcol1"; }
            else if (code === 42) { this.bgcol = "bgcol2"; }
            else if (code === 43) { this.bgcol = "bgcol3"; }
            else if (code === 44) { this.bgcol = "bgcol4"; }
            else if (code === 45) { this.bgcol = "bgcol5"; }
            else if (code === 46) { this.bgcol = "bgcol6"; }
            else if (code === 47) { this.bgcol = "bgcol7"; }
            else if (code === 49) { this.bgcol = null; }
            else {
                // unknown SGR code
                console.log("unimplemented SGR code " + code);
            }
        }
    }
}
