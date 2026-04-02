import { useEffect, useState } from 'react'

const OTHER_OPTION_VALUE = '__other__'

interface SelectOrCustomInputFieldProps {
  label: string
  options: string[]
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  selectPlaceholder: string
  customPlaceholder: string
}

function isKnownOption(options: string[], value: string) {
  return value !== '' && options.includes(value)
}

export default function SelectOrCustomInputField({
  label,
  options,
  value,
  onChange,
  disabled = false,
  selectPlaceholder,
  customPlaceholder,
}: SelectOrCustomInputFieldProps) {
  const [isCustomMode, setIsCustomMode] = useState(() => !isKnownOption(options, value) && value !== '')

  useEffect(() => {
    if (isKnownOption(options, value)) {
      setIsCustomMode(false)
      return
    }

    if (value !== '') {
      setIsCustomMode(true)
    }
  }, [options, value])

  const handleSelectChange = (nextValue: string) => {
    if (nextValue === OTHER_OPTION_VALUE) {
      setIsCustomMode(true)
      if (isKnownOption(options, value)) {
        onChange('')
      }
      return
    }

    setIsCustomMode(false)
    onChange(nextValue)
  }

  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <select
        className="form-select"
        value={isCustomMode ? OTHER_OPTION_VALUE : value}
        onChange={event => handleSelectChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">{selectPlaceholder}</option>
        {options.map(option => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
        <option value={OTHER_OPTION_VALUE}>Khác</option>
      </select>
      {isCustomMode && (
        <input
          type="text"
          className="form-input"
          value={value}
          onChange={event => onChange(event.target.value)}
          placeholder={customPlaceholder}
          disabled={disabled}
        />
      )}
    </div>
  )
}
