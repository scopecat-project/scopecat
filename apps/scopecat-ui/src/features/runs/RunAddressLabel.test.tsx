// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { RunAddressLabel } from "./RunAddressLabel";

afterEach(cleanup);

it("keeps readable names and local numbers primary while disambiguating duplicate names", () => {
  render(
    <>
      <RunAddressLabel
        collectionName="Cooldown"
        address={{ collection_id: "a", number: 1, run_id: "run-a" }}
        sequence={9}
      />
      <RunAddressLabel
        collectionName="Cooldown"
        address={{ collection_id: "b", number: 1, run_id: "run-b" }}
        sequence={12}
      />
    </>,
  );
  const addresses = screen.getAllByTestId("run-address");
  expect(addresses.map((item) => item.textContent)).toEqual(["Cooldown · #1", "Cooldown · #1"]);
  expect(addresses[0]).toHaveAttribute("title", "Collection ID: a\nGlobal #9\nRun ID: run-a");
  expect(addresses[1]).toHaveAttribute("title", "Collection ID: b\nGlobal #12\nRun ID: run-b");
});

it("shows missing names and addresses explicitly instead of inventing a number", () => {
  const view = render(
    <RunAddressLabel address={{ collection_id: "a", number: 1, run_id: "run-a" }} />,
  );
  expect(screen.getByTestId("run-address")).toHaveTextContent("Collection name unavailable · #1");
  view.rerender(<RunAddressLabel sequence={9} />);
  expect(screen.getByTestId("run-address")).toHaveTextContent("Collection address unavailable");
});
