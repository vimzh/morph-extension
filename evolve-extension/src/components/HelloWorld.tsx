import { useState } from 'react'

export default function HelloWorld(props: { msg: string }) {
  const [count, setCount] = useState(0)

  return (
    <>
      <h1>{props.msg}</h1>

      <div className="card">
        <button type="button" onClick={() => setCount(count + 1)}>
          count is
          {' '}
          {count}
        </button>
        <p>
          Edit
          <code>src/components/HelloWorld.tsx</code>
          {' '}
          to test HMR
        </p>
      </div>

      <p>
        Check out
        <a href="https://github.com/nicedeng/evolve-browser" target="_blank" rel="noreferrer">Evolve Browser</a>
        , the official repo
      </p>

      <p className="read-the-docs">
        Click on the Vite and React logos to learn more
      </p>
    </>
  )
}
