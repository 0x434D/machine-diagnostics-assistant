import { Chat } from "./Chat";

export function App() {
  return (
    <>
      <header>
        <h1>Machine Diagnostics</h1>
        <p>
          Read-only. This answers from what the line recorded, and it cannot act
          on the plant.
        </p>
      </header>
      <Chat />
    </>
  );
}
