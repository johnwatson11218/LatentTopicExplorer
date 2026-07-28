"""
html_color_names.py

Maps high-level color categories to the 140 CSS/HTML named colors
as listed on https://htmlcolorcodes.com/color-names/

Usage:
    from html_color_names import COLOR_GROUPS, get_colors

    print(COLOR_GROUPS["red"])
    print(get_colors("blue"))
"""

from typing import Dict, List
import psycopg2
from psycopg2.extensions import connection as PGConnection

def get_db_connection( 
    host: str = "192.168.86.242",
    port: int = 5432,
    dbname: str = "second_brain",
    user: str = "postgres",
    password: str = "test_case",
                   
) -> PGConnection:
    return  psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )


COLOR_GROUPS: Dict[str, List[str]] = {
    "red": [
        "IndianRed",
        "LightCoral",
        "Salmon",
        "DarkSalmon",
        "LightSalmon",
        "Crimson",
        "Red",
        "FireBrick",
        "DarkRed",
    ],
    "pink": [
        "Pink",
        "LightPink",
        "HotPink",
        "DeepPink",
        "MediumVioletRed",
        "PaleVioletRed",
    ],
    "orange": [
        "LightSalmon",
        "Coral",
        "Tomato",
        "OrangeRed",
        "DarkOrange",
        "Orange",
    ],
    "yellow": [
        "Gold",
        "Yellow",
        "LightYellow",
        "LemonChiffon",
        "LightGoldenrodYellow",
        "PapayaWhip",
        "Moccasin",
        "PeachPuff",
        "PaleGoldenrod",
        "Khaki",
        "DarkKhaki",
    ],
    "purple": [
        "Lavender",
        "Thistle",
        "Plum",
        "Violet",
        "Orchid",
        "Fuchsia",
        "Magenta",
        "MediumOrchid",
        "MediumPurple",
        "RebeccaPurple",
        "BlueViolet",
        "DarkViolet",
        "DarkOrchid",
        "DarkMagenta",
        "Purple",
        "Indigo",
        "SlateBlue",
        "DarkSlateBlue",
        "MediumSlateBlue",
    ],
    "green": [
        "GreenYellow",
        "Chartreuse",
        "LawnGreen",
        "Lime",
        "LimeGreen",
        "PaleGreen",
        "LightGreen",
        "MediumSpringGreen",
        "SpringGreen",
        "MediumSeaGreen",
        "SeaGreen",
        "ForestGreen",
        "Green",
        "DarkGreen",
        "YellowGreen",
        "OliveDrab",
        "Olive",
        "DarkOliveGreen",
        "MediumAquamarine",
        "DarkSeaGreen",
        "LightSeaGreen",
        "DarkCyan",
        "Teal",
    ],
    "blue": [
        "Aqua",
        "Cyan",
        "LightCyan",
        "PaleTurquoise",
        "Aquamarine",
        "Turquoise",
        "MediumTurquoise",
        "DarkTurquoise",
        "CadetBlue",
        "SteelBlue",
        "LightSteelBlue",
        "PowderBlue",
        "LightBlue",
        "SkyBlue",
        "LightSkyBlue",
        "DeepSkyBlue",
        "DodgerBlue",
        "CornflowerBlue",
        "MediumSlateBlue",
        "RoyalBlue",
        "Blue",
        "MediumBlue",
        "DarkBlue",
        "Navy",
        "MidnightBlue",
    ],
    "brown": [
        "Cornsilk",
        "BlanchedAlmond",
        "Bisque",
        "NavajoWhite",
        "Wheat",
        "BurlyWood",
        "Tan",
        "RosyBrown",
        "SandyBrown",
        "Goldenrod",
        "DarkGoldenrod",
        "Peru",
        "Chocolate",
        "SaddleBrown",
        "Sienna",
        "Brown",
        "Maroon",
    ],
    "white": [
        "White",
        "Snow",
        "HoneyDew",
        "MintCream",
        "Azure",
        "AliceBlue",
        "GhostWhite",
        "WhiteSmoke",
        "SeaShell",
        "Beige",
        "OldLace",
        "FloralWhite",
        "Ivory",
        "AntiqueWhite",
        "Linen",
        "LavenderBlush",
        "MistyRose",
    ],
    "gray": [
        "Gainsboro",
        "LightGray",
        "Silver",
        "DarkGray",
        "Gray",
        "DimGray",
        "LightSlateGray",
        "SlateGray",
        "DarkSlateGray",
        "Black",
    ],
}

# Convenience aliases (case-insensitive lookup helpers)
_ALIASES = {
    "grey": "gray",
    "greys": "gray",
    "grays": "gray",
}


def get_colors(category: str) -> List[str]:
    """
    Return the list of named colors for a high-level category.

    Parameters
    ----------
    category : str
        One of: red, pink, orange, yellow, purple, green, blue, brown, white, gray
        (case-insensitive; 'grey' is accepted as an alias for 'gray')

    Returns
    -------
    list[str]
        The CSS/HTML color names belonging to that group.

    Raises
    ------
    KeyError
        If the category is not recognised.
    """
    key = category.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in COLOR_GROUPS:
        raise KeyError(
            f"Unknown color category: {category!r}. "
            f"Valid keys: {', '.join(sorted(COLOR_GROUPS))}"
        )
    return COLOR_GROUPS[key].copy()


def all_categories() -> List[str]:
    """Return the list of high-level category names."""
    return list(COLOR_GROUPS.keys())


def all_named_colors() -> List[str]:
    """Return a flat list of every named color (order is category order)."""
    seen = set()
    result = []
    for colors in COLOR_GROUPS.values():
        for c in colors:
            if c not in seen:
                seen.add(c)
                result.append(c)
    return result


if __name__ == "__main__":
    # Quick demo
    # print("Available categories:", all_categories())
    # print()
    # print("Red colors:", get_colors("red"))
    # print()
    # print("Total unique named colors:", len(all_named_colors()))
    conn = get_db_connection()
    # get a list of all the category_ids
    cur = conn.cursor()
    cur.execute( "select c.id from categories c ")    
    ids = [ r[0] for r in cur.fetchall()]
    colors = all_named_colors()
    for i in range( len( ids )):
        id = ids[i]
        color = colors[ i % len( colors )]    
        cur.execute( "update document_categories set color = %s where category_id = %s ", ( color, id, ))
    conn.commit()
    cur.close()
    conn.close()