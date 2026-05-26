# <>-<> ABOUT PYDANTIC ERRORS <>-<>

Pydantic contains an `errors()` method that returns a list of errors
that were encountered during validation. This is a list of
`ErrorDetails` objects, which are a dict with the following keys:

- `type`: The type of error that occurred, machine-readable
- `loc`: tuple of (str, int) identifying where in the schema the error occurred.
  the str is the name of the field (the key), and the int is {???}
- `msg`: A human readable error message.
- `input`: The input data at this `loc` that caused the error.
- `ctx`: Values which are required to render the error message, and could hence be useful in
  rendering custom error messages. Also useful for passing custom error data forward.
- `url`: The documentation URL giving information about the error. No URL is available if
  a [`PydanticCustomError`][pydantic_core.PydanticCustomError] is used.

## ABOUT 'loc'

The loc tuple represents the path to the field that failed validation, tracing through
your nested data structure from the root down to the exact problem location. Each
element in the tuple is one step deeper into the nesting. For simple fields, it's just
the field name like ('username',). For nested models, it shows the path through field
names like ('username', 'address', 'zip_code'). When validating sequences like lists,
an integer index appears in the path to indicate which element failed, like
('items', 2, 'price') for an error in the third item's price field.

## <>-<> Some common scenarios <>-<>

**"Required field not found"**
You get type: "missing", msg is something like "Field required".

**"Field not in the model"**
By default pydantic-settings just silently ignores extra fields. If you want
it to error, you need model_config = SettingsConfigDict(extra="forbid"),
then you'll get type: "extra_forbidden".

**"Field is empty"** (e.g. host = "")
This changes depending on the type. A str field will accept "" with no error. If
you want to reject empty strings you need to add a validator like min_length=1
or a @field_validator. A None value on a non-optional field gives you
type: "missing" or type: "none_required" depending on context.

**"Field is invalid"** (e.g. port = "banana" for an int field)
You get type: "int_parsing" or similar, and msg like
"Input should be a valid integer".
