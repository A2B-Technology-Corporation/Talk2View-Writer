---
name: table-editing
description: Create, populate, and modify tables in Word documents. Use when the user asks to build a table, add or edit rows/columns, update cell content, convert text to a table, or restructure tabular data. Covers both table creation with insert_table and post-creation modifications with edit_table.
metadata:
  author: talk2view-word
  version: "2.0"
---

# Table Editing

## When to use

- User asks to create a table, chart, matrix, or grid
- User asks to add data in tabular form
- User asks to edit, update, or change a cell in an existing table
- User asks to add or remove rows or columns
- User provides data that naturally fits a table (comparisons, schedules, lists with multiple attributes)

## Key rules

### 1. Two tools: `insert_table` for creation, `edit_table` for modification

- `insert_table` — creates a new table with data in one call
- `edit_table` — modifies an existing table (edit cells, add/delete rows, add/delete columns)

Always check `get_document()` first — it returns table metadata (index, rows, columns, first row preview) so you know what tables exist.

### 2. For new tables: prepare data upfront

Pass a complete 2D string array to `insert_table`. The first row is the header.

```
insert_table(
  rows=4, columns=3, location="end",
  data=[
    ["Name", "Role", "Department"],
    ["Alice", "Engineer", "Platform"],
    ["Bob", "Designer", "Product"],
    ["Carol", "Manager", "Platform"]
  ]
)
```

### 3. For existing tables: use `edit_table`

Read the document first to find the table index, then modify:

```
get_document()
// Response includes: tables: [{ index: 0, rows: 4, columns: 3, first_row: ["Name", "Role", "Department"] }]

edit_table(table_index=0, action="edit_cell", row=1, column=2, value="Engineering")
edit_table(table_index=0, action="add_rows", insert_location="end", count=1, values=[["Dave", "Analyst", "Finance"]])
edit_table(table_index=0, action="delete_rows", row=2, count=1)
```

### 4. Row and column counts must match your data

For `insert_table`, `rows` and `columns` define dimensions. Your `data` array should match: `rows = data.length`, `columns = data[0].length`.

### 5. Column operations require uniform tables

`add_columns` and `delete_columns` only work on tables where all rows have the same number of cells. Check `get_document()` first — if the table isn't uniform, use `edit_cell` to modify individual cells instead.

## Step-by-step: Create a table

1. **Read the document for context:**
   ```
   get_document()
   ```

2. **Add a heading (if appropriate):**
   ```
   insert_content(text="Quarterly Results", location="end", style="Heading2")
   ```

3. **Insert the table with data:**
   ```
   insert_table(rows=5, columns=3, location="end", data=[
     ["Quarter", "Revenue", "Growth"],
     ["Q1", "$1.2M", "+5%"],
     ["Q2", "$1.4M", "+17%"],
     ["Q3", "$1.1M", "-21%"],
     ["Q4", "$1.6M", "+45%"]
   ])
   ```

4. **Add a caption below:**
   ```
   insert_content(text="Table 1: Quarterly revenue and growth rates.", location="end")
   ```

## Step-by-step: Modify an existing table

1. **Read the document to find the table:**
   ```
   get_document()
   // tables: [{ index: 0, rows: 5, columns: 3, first_row: ["Quarter", "Revenue", "Growth"] }]
   ```

2. **Edit a cell:**
   ```
   edit_table(table_index=0, action="edit_cell", row=4, column=1, value="$1.8M")
   ```

3. **Add a row:**
   ```
   edit_table(table_index=0, action="add_rows", insert_location="end", count=1, values=[["Q5", "$2.0M", "+11%"]])
   ```

4. **Delete a row:**
   ```
   edit_table(table_index=0, action="delete_rows", row=3, count=1)
   ```

## Common table patterns

### Comparison matrix
```
data=[
  ["Feature", "Plan A", "Plan B", "Plan C"],
  ["Storage", "10 GB", "100 GB", "Unlimited"],
  ["Users", "1", "5", "Unlimited"],
  ["Support", "Email", "Phone", "Dedicated"],
  ["Price", "$10/mo", "$25/mo", "$99/mo"]
]
```

### Schedule/timeline
```
data=[
  ["Date", "Milestone", "Owner"],
  ["Jan 15", "Kickoff", "Team Lead"],
  ["Feb 1", "Design Review", "Designer"],
  ["Mar 15", "Beta Release", "Engineering"]
]
```

### Key-value pairs (2-column)
```
data=[
  ["Property", "Value"],
  ["Project", "Website Redesign"],
  ["Status", "In Progress"],
  ["Due Date", "March 30, 2026"],
  ["Budget", "$50,000"]
]
```

## Common mistakes

| Mistake | Why it's wrong | Fix |
|---------|---------------|-----|
| Not calling `get_document` before editing | You don't know the table index or dimensions | Always read first to get table metadata |
| Mismatched `rows`/`columns` vs data dimensions | Empty cells or truncated data | `rows = data.length`, `columns = data[0].length` |
| Putting long paragraphs in cells | Tables become unreadable; Word wraps awkwardly | Keep cell content to 1-2 short phrases |
| Forgetting the header row in the count | Table has one fewer data row than expected | `rows` = number of data items + 1 (for header) |
| Using `add_columns` on a non-uniform table | Will fail — Word API requires uniform tables for column ops | Use `edit_cell` to modify individual cells instead |
| Recreating a table to change one cell | Wasteful and loses any formatting | Use `edit_table(action="edit_cell")` to update in place |
