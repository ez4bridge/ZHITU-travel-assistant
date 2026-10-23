"""Planning allowances, separate from carrier departure/arrival clock times."""


def arrival_buffer(mode, explicit=None):
    # Rail/bus arrivals do not require a universal 30-minute waiting period.
    # A visible station handoff node and actual local route still take time.
    # Flying retains a conservative exit/baggage allowance. User input wins,
    # including an explicit zero; none of these are official minimums.
    return explicit if explicit is not None else (60 if mode == 'flight' else 0)


def departure_buffer(mode, explicit=None):
    # How early the traveller reaches the station/airport before departure.
    # User input wins, including an explicit zero; these are planning
    # allowances, not carrier minimum check-in rules.
    return explicit if explicit is not None else (90 if mode == 'flight' else 30)
