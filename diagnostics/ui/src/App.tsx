import { AuthProvider } from "./AuthContext";
import { Chat } from "./Chat";
import { TokenField } from "./TokenField";

export function App() {
  return (
    <AuthProvider>
      <header>
        <h1>Machine Diagnostics</h1>
        <p>
          Read-only. This answers from what the line recorded, and it cannot act
          on the plant.
        </p>
        <TokenField />
      </header>
      <Chat />
    </AuthProvider>
  );
}
