"""
Status badge generation
"""

import argparse
import inspect


class BadgeGenerator:
    """
    used for generating status badges.
    such a badge is usually embedded in a website so it shows
    the CI status with a shiny color.
    """

    def __init__(self, text0, text1, right_color=None):
        """
        store the badge texts.
        text0 is the first part, text1 the second.
        """
        self._text0 = text0
        self._text1 = text1

        self.right_color = right_color

    def get_svg(self):
        """
        simple svg badge generation
        """

        text0 = self._text0
        text1 = self._text1

        # gray, the left part
        color0 = ("#444d56", "#1c1f22")

        # right part is customizable
        if self.right_color == "green":
            color1 = ("#34d058", "#269a3e")
        elif self.right_color == "red":
            color1 = ("#c65c64", "#cb2431")
        elif self.right_color == "blue":
            color1 = ("#008bc6", "#005eb6")
        else:
            raise Exception(f"unknown color scheme requested: {self.right_color!r}")

        # definitely correct text size calculation
        # determined by trial and error
        # only works for "usual" text lengths
        width0 = len(text0) * 6.8 + 10
        width1 = len(text1) * 6.8 + 10

        # best view and edit the svg with inkscape to test adjustments live...
        svg_content = inspect.cleandoc(f"""
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
             width="{width0 + width1}" height="20" role="img" aria-label="{text0 + ' ' + text1}">

          <metadata>
            <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns"
                     xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema"
                     xmlns:dc="http://purl.org/dc/elements/1.1/">
              <rdf:Description dc:title="Kevin Badge"
                               dc:description="Kevin Continuous Integration Status Badge"
                               dc:publisher="SFTtech"
                               dc:date="2021-10-31"
                               dc:format="image/svg+xml"
                               dc:creator="SFT Kevin CI"
                               dc:rights="CC-BY-SA 4.0"
                               dc:language="en">
              </rdf:Description>
            </rdf:RDF>
          </metadata>

          <title>{text0 + ' ' + text1}</title>

          <defs>
            <linearGradient id="leftfill" x1="50%" y1="0%" x2="50%" y2="100%">
              <stop offset="0%" stop-color="{color0[0]}" stop-opacity="1"></stop>
              <stop offset="100%" stop-color="{color0[1]}" stop-opacity="1"></stop>
            </linearGradient>
            <linearGradient id="rightfill" x1="50%" y1="0%" x2="50%" y2="100%">
              <stop offset="0%" stop-color="{color1[0]}" stop-opacity="1"></stop>
              <stop offset="100%" stop-color="{color1[1]}" stop-opacity="1"></stop>
            </linearGradient>
          </defs>

          <clipPath id="round">
            <rect width="{width0 + width1}" height="20" rx="3" fill="#fff"/>
          </clipPath>

          <g clip-path="url(#round)" id="surface">
            <rect id="leftsurface" width="{width0}" height="20" fill="url(#leftfill)"/>
            <rect id="rightsurface" x="{width0}" width="{width1}" height="20" fill="url(#rightfill)"/>
          </g>

          <g id="texts"
             fill="#fff"
             text-anchor="middle"
             font-family="DejaVu Sans,Verdana,Geneva,sans-serif"
             text-rendering="geometricPrecision" font-size="11">

            <text id="leftshadow" x="{width0/2}" y="15"
                  pointer-events="none"
                  transform="scale(1)"
                  fill="#010101" fill-opacity=".3">
            {text0}
            </text>
            <text id="lefttext" x="{width0/2}" y="14"
                  transform="scale(1)"
                  fill="#fff">
            {text0}
            </text>
            <text id="rightshadow"
                  x="{width0 + width1/2 - 1}" y="15"
                  pointer-events="none"
                  transform="scale(1)"
                  fill="#010101" fill-opacity=".3">
            {text1}
            </text>
            <text id="righttext"
                  x="{width0 + width1/2 - 1}" y="14"
                  transform="scale(1)"
                  fill="#fff">
            {text1}
            </text>
          </g>
        </svg>
        """)
        return svg_content


def main():
    """
    for testing, create a svg with any text
    """
    cli = argparse.ArgumentParser(description='')
    cli.add_argument("--color", "-c", choices=['red', 'blue', 'green'],
                     default="blue",
                     help=("right hand side background color, "
                           "default=%(default)s"))
    cli.add_argument("text0")
    cli.add_argument("text1")

    args = cli.parse_args()

    badge = Badge(args.text0, args.text1, args.color)

    print(badge.get_svg())


if __name__ == "__main__":
    main()
