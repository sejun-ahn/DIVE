def add_bool_arg(parser, name, default=False, help=None):
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument(f'--{name}', dest=name, action='store_true', help=help)
    group.add_argument(f'--no-{name}', dest=name, action='store_false', help=help)
    parser.set_defaults(**{name: default})