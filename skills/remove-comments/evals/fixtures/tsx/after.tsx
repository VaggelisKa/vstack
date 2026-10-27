import { useEffect, useState } from "react";

type Props = {
  users: { id: string; name: string }[];
  onSelect: (id: string) => void;
};

export function UserList({ users, onSelect }: Props) {
  const [filter, setFilter] = useState("");

  useEffect(() => {
    document.getElementById("search")?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- focus once, on mount only
  }, []);

  return (
    <div className="user-list">
      <input
        id="search"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <ul>
        {users
          .filter((u) => u.name.includes(filter))
          .map((u) => (
            // Key by id, not index: drag-and-drop reorders rows.
            <li key={u.id} onClick={() => onSelect(u.id)}>
              {u.name}
            </li>
          ))}
      </ul>
    </div>
  );
}
