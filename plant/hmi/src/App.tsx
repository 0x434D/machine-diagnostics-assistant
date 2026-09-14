import { Line } from "./Line";
import { useLineSnapshot } from "./useLineSnapshot";

export function App() {
  const { snapshot, connected } = useLineSnapshot();

  return (
    <>
      <header>
        <h1>Plant line</h1>
        <p>
          Colour is the category, not the state: amber is a station waiting on
          someone else, red is a station to go and look at.
        </p>
        <p className={connected ? "link link--up" : "link link--down"}>
          {connected ? "connected" : "not connected to the simulator"}
        </p>
      </header>
      {snapshot === null ? (
        <p className="waiting">waiting for the first frame…</p>
      ) : (
        <Line snapshot={snapshot} />
      )}
    </>
  );
}
