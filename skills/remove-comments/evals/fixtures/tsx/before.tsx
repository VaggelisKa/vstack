import { useEffect, useState } from "react";

// New: UserList component
type Props = {
  // the users to show
  users: { id: string; name: string }[];
  onSelect: (id: string) => void; // callback
};

/**
 * UserList component.
 * @param props the props
 * @returns the JSX
 */
export function UserList({ users, onSelect }: Props) {
  // State for the filter
  const [filter, setFilter] = useState("");

  useEffect(() => {
    // Focus the search box on mount
    document.getElementById("search")?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- focus once, on mount only
  }, []);

  return (
    <div className="user-list">
      {/* Search input */}
      <input
        id="search"
        value={filter}
        // update the filter
        onChange={(e) => setFilter(e.target.value)}
      />
      <ul>
        {users
          .filter((u) => u.name.includes(filter)) // filter users
          .map((u) => (
            // The key must be the stable id and not the index, because rows can be
            // reordered by drag-and-drop and React would reuse the wrong DOM nodes.
            <li key={u.id} onClick={() => onSelect(u.id)}>
              {u.name} {/* the name */}
            </li>
          ))}
      </ul>
      {/* <Pagination page={page} /> */}
    </div>
  );
}
