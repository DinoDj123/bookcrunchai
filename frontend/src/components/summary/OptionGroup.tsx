"use client";

import { useId } from "react";

type Props<T extends string> = {
  legend: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
};

/** A choice of one option out of a few, shown as a row of buttons. Underneath it is a group of
 *  radio buttons, so the keyboard (arrow keys) and screen readers work without extra code. */
export function OptionGroup<T extends string>({ legend, options, value, onChange }: Props<T>) {
  const name = useId();
  return (
    <fieldset>
      <legend className="text-muted-foreground mb-1.5 text-sm">{legend}</legend>
      <div className="bg-surface inline-flex rounded-lg p-1">
        {options.map((option) => (
          <label
            key={option.value}
            className="text-muted-foreground has-checked:bg-background has-checked:text-foreground has-focus-visible:ring-ring/50 flex h-9 cursor-pointer items-center rounded-md px-3 text-sm font-medium has-checked:shadow-sm has-focus-visible:ring-3"
          >
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="sr-only"
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}
