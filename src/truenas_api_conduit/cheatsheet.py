from rich.table import Table
from rich.style import Style

command_style = Style(
    color="deep_sky_blue1",
    bold=True,
)
desc_style = Style(
    italic=True,
)

table = Table(
    style=Style(
        color="green4",
        bold=True,
    ),
    padding=(0, 2),
)

table.add_column("TrueNAS API Cheatsheet")
# =============
table.add_row("truenas-api request core.ping", style=command_style)
table.add_row("Simple ping", style=desc_style, end_section=True)
# =============
table.add_row("truenas-api request system.info", style=command_style)
table.add_row("Get basic system info", style=desc_style, end_section=True)
# =============
table.add_row("truenas-api request pool.query", style=command_style)
table.add_row("Get data for all pools", style=desc_style, end_section=True)
# =============
table.add_row(
    "truenas-api request disk.query --filter name = sda",
    style=command_style,
)
table.add_row(
    "Get info for disk with name of 'sda'", style=desc_style, end_section=True
)
# =============
table.add_row(
    "truenas-api request disk.query --filter size '>' 100",
    style=command_style,
)
table.add_row(
    "Get info for all disks with size > 100.\nNote how the operator is in quotes "
    "for bash compatibility",
    style=desc_style,
    end_section=True,
)
# =============
table.add_row(
    "truenas-api request disk.query -f 'size' '>' '100'",
    style=command_style,
)
table.add_row(
    "Same as above, but with FIELD OPERATOR VALUE all in quotes",
    style=desc_style,
    end_section=True,
)
# =============
table.add_row(
    "truenas-api request disk.query -f name rin sda",
    style=command_style,
)
table.add_row(
    "Get info for disks whose name contains the string 'sda' (ie. sda1, etc.)",
    style=desc_style,
    end_section=True,
)
# =============
table.add_row(
    "truenas-api request pool.query -f free '>' 1000 -f status = ONLINE",
    style=command_style,
)
table.add_row(
    "Get info for pools with free space > 1000 and status = ONLINE",
    style=desc_style,
    end_section=True,
)
# =============
table.add_row(
    """truenas-api request disk.query --params '[["name", "=", "sda"]]'""",
    style=command_style,
)
table.add_row("Manually doing what --filter does under the hood", style=desc_style, end_section=True)
# =============
table.add_row(
    """truenas-api request pool.query --params '[["free", ">", 1000], ["status", "=", "ONLINE"]]'""",
    style=command_style,
)
table.add_row(
    "Passing in a list of filters. Each filter is a triplet array.\nNote the number "
    "1000 is not in quotes so its treated as an int", style=desc_style, end_section=True
)

table2 = Table(
    style=Style(
        color="green4",
        bold=True,
    ),
    padding=(0, 2),
)

table2.add_column("Examples using curl (Change address/port as needed)")
# =============
table2.add_row("curl http://localhost:4567/status | jq ", style=command_style)
table2.add_row("Get service status and pipe results into jq", style=desc_style, end_section=True)
# =============
table2.add_row(r"""curl -X POST http://localhost:4567/rpc -H 'Content-Type: application/json' -d '{"method": "core.ping", "params": []}' """, style=command_style)
table2.add_row("Simple ping", style=desc_style, end_section=True)
# =============
table2.add_row(r"""curl -X POST http://localhost:4567/rpc -H 'Content-Type: application/json' -d '{"method": "disk.query", "params": [[["name", "=", "sda"]]]}' | jq """, style=command_style)
table2.add_row("Disk query with filter, results piped into jq", style=desc_style, end_section=True)
# =============
